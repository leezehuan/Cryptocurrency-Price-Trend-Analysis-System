"""内容源同步服务（Gate Square / Gate News）。

负责热门帖子、关注用户帖子和新闻的同步与落库。
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import httpx

from .database import utc_now
from .gate_mcp import (
    GateMCPClient,
    _square_tool_names,
    _square_tool_schema,
    parse_json_from_content,
    safe_call_tool,
)

logger = logging.getLogger(__name__)
SQUARE_SEARCH_QUERY = "BTC 比特币"

def _first_value(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None

def _nested_value(mapping: dict[str, Any], keys: tuple[str, ...], nested_keys: tuple[str, ...]) -> Any:
    value = _first_value(mapping, keys)
    if isinstance(value, dict):
        return _first_value(value, nested_keys)
    return value

def _coerce_time(value: Any, fallback: str) -> str:
    if value in (None, ""):
        return fallback
    if isinstance(value, (int, float)) or str(value).isdigit():
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(microsecond=0).isoformat()
    return str(value)

def _contains_chinese(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)

def _square_hot_rank(post: dict[str, Any]) -> float:
    return (
        float(post.get("hot_score") or 0)
        + float(post.get("likes") or 0) * 0.2
        + float(post.get("comments") or 0) * 0.5
        + float(post.get("repost_count") or 0) * 0.8
    )

def _square_preview(posts: list[dict[str, Any]], limit: int = 3) -> list[str]:
    return [str(post.get("content") or "").replace("\n", " ")[:160] for post in posts[:limit]]

def _translate_posts_to_chinese(posts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """将英文帖子翻译为中文，返回 (翻译后帖子列表, 翻译条数)。

    已含中文的帖子不做翻译；翻译失败时保留原文。
    """
    if not posts:
        return posts, 0
    need_translate = [p for p in posts if not _contains_chinese(str(p.get("content") or ""))]
    if not need_translate:
        return posts, 0
    try:
        from .llm_client import active_provider_config
        provider = active_provider_config()
        base_url = str(provider.get("base_url") or "").rstrip("/")
        api_key = str(provider.get("api_key") or "")
        model = str(provider.get("chat_model") or "")
        if not base_url or not model:
            logger.warning("Square translate skipped: LLM not configured")
            return posts, 0
    except Exception as exc:
        logger.warning("Square translate skipped: %s", exc)
        return posts, 0
    translated_count = 0
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}" if api_key else "",
    }
    timeout_sec = int(provider.get("timeout_seconds", 30))
    max_tokens = min(int(provider.get("max_tokens", 1200)), 2000)
    for post in need_translate:
        original = str(post.get("content") or "")
        if not original.strip():
            continue
        try:
            body = {
                "model": model,
                "messages": [
                    {"role": "system", "content": "你是一个翻译助手。将以下英文加密货币/区块链相关帖子翻译成自然流畅的中文。只输出翻译结果，不要添加解释或额外内容。如果内容已经是中文则原样返回。"},
                    {"role": "user", "content": original[:3000]},
                ],
                "temperature": 0.3,
                "max_tokens": max_tokens,
            }
            with httpx.Client(timeout=timeout_sec) as llm_client:
                resp = llm_client.post(f"{base_url}/chat/completions", json=body, headers=headers)
                resp.raise_for_status()
            result = resp.json()
            translated = result["choices"][0]["message"]["content"].strip()
            if translated and len(translated) > len(original) * 0.3:
                post["content"] = translated[:4000]
                post["source"] = post.get("source", "gate_square") + "_translated"
                translated_count += 1
        except Exception as exc:
            logger.debug("Square post translate failed: %s", exc)
            continue
    return posts, translated_count

def _extract_square_items(data: Any, depth: int = 0) -> list[Any]:
    if depth > 4:
        return []
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("posts", "items", "data", "list", "result", "records", "rows"):
        value = data.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = _extract_square_items(value, depth + 1)
            if nested:
                return nested
    for value in data.values():
        if isinstance(value, (dict, list)):
            nested = _extract_square_items(value, depth + 1)
            if nested:
                return nested
    return []

def _square_text_post(text: str, now: str) -> dict[str, Any] | None:
    content = text.strip()
    if not content:
        return None
    digest = hashlib.sha1(content[:4000].encode("utf-8")).hexdigest()[:16]
    return {
        "post_id": f"square_ai_search:{digest}",
        "author": "Gate Square AI Search",
        "author_id": "",
        "content": content[:4000],
        "publish_time": now,
        "likes": 0,
        "comments": 0,
        "repost_count": 0,
        "tags": [],
        "hot_score": 0,
        "source": "gate_square_ai_search",
    }

def _normalize_square_post(post: dict[str, Any], now: str) -> dict[str, Any] | None:
    content_value = _first_value(post, ("content", "text", "body", "summary", "description", "title"))
    if content_value in (None, ""):
        content_value = json.dumps(post, ensure_ascii=False, default=str)
    content = str(content_value).strip()
    if not content:
        return None
    post_id = str(_first_value(post, ("id", "post_id", "feed_id", "topic_id", "article_id", "url")) or "")
    if not post_id:
        digest = hashlib.sha1(content[:4000].encode("utf-8")).hexdigest()[:16]
        post_id = f"square_post:{digest}"
    tags = _first_value(post, ("tags", "labels", "topics")) or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except (json.JSONDecodeError, TypeError):
            tags = [tags]
    author = _nested_value(post, ("author", "user", "user_info", "author_info"), ("name", "nickname", "user_name", "display_name"))
    author_id = _nested_value(post, ("author_id", "user_id", "uid", "user", "user_info", "author_info"), ("id", "user_id", "uid"))
    return {
        "post_id": post_id,
        "author": str(author or "Gate Square"),
        "author_id": str(author_id or ""),
        "content": content[:4000],
        "publish_time": _coerce_time(_first_value(post, ("publish_time", "created_at", "create_time", "time", "timestamp")), now),
        "likes": _int(_first_value(post, ("likes", "like_count", "likes_count"))),
        "comments": _int(_first_value(post, ("comments", "comment_count", "comments_count"))),
        "repost_count": _int(_first_value(post, ("reposts", "repost_count", "share_count"))),
        "tags": tags if isinstance(tags, list) else [],
        "hot_score": _float(_first_value(post, ("hot_score", "score", "weight"))) or 0,
        "source": "gate_square",
    }

def _normalize_square_posts(data: Any, now: str) -> list[dict[str, Any]]:
    if isinstance(data, str):
        text_post = _square_text_post(data, now)
        return [text_post] if text_post else []
    items = _extract_square_items(data)
    posts: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            normalized = _normalize_square_post(item, now)
        elif isinstance(item, str):
            normalized = _square_text_post(item, now)
        else:
            normalized = None
        if normalized:
            posts.append(normalized)
    return posts

def _square_search_arguments(client: GateMCPClient, endpoint: str, tool_name: str, limit: int) -> dict[str, Any]:
    schema = _square_tool_schema(client, endpoint, tool_name)
    properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
    required = schema.get("required") if isinstance(schema.get("required"), list) else []
    if not properties:
        return {"keyword": SQUARE_SEARCH_QUERY, "limit": limit}
    args: dict[str, Any] = {}
    for name in ("limit", "page_size", "size", "per_page"):
        if name in properties:
            args[name] = limit
            break
    for name in ("keyword", "query", "q", "search", "text", "content", "prompt"):
        if name in properties:
            args[name] = SQUARE_SEARCH_QUERY
            break
    for name in ("language", "lang", "locale"):
        if name in properties:
            args[name] = "zh-CN"
            break
    for name in ("sort", "sort_by", "order_by", "order"):
        if name in properties:
            args[name] = "hot"
            break
    for name in required:
        if name in args:
            continue
        prop = properties.get(name) if isinstance(properties.get(name), dict) else {}
        value_type = prop.get("type")
        if value_type in ("integer", "number"):
            args[name] = limit
        elif value_type == "boolean":
            args[name] = False
        else:
            args[name] = SQUARE_SEARCH_QUERY
    return args

def _square_hot_tool_candidates(client: GateMCPClient, limit: int) -> list[tuple[str, str, dict[str, Any]]]:
    mcp_names = _square_tool_names(client, "mcp")
    candidates: list[tuple[str, str, dict[str, Any]]] = []
    tool_names = ("cex_square_list_square_ai_search", "list_square_ai_search", "square_ai_search")
    for tool_name in tool_names:
        if mcp_names and tool_name not in mcp_names:
            continue
        candidates.append(("mcp", tool_name, _square_search_arguments(client, "mcp", tool_name, limit)))
        for key in ("keyword", "query", "content", "q"):
            args = {key: SQUARE_SEARCH_QUERY, "limit": limit}
            if args not in [item[2] for item in candidates if item[1] == tool_name]:
                candidates.append(("mcp", tool_name, args))
    candidates.append(("mcp/info", "get_square_hot", {"limit": limit}))
    return candidates

def sync_gate_news(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP /mcp/news 拉取配置关键词相关新闻，存入 gate_mcp_raw_records 作为原始记录。"""
    from .services import get_setting_value

    if client is None:
        client = _default_client(conn)

    keywords = get_setting_value(conn, "news.keywords", ["BTC", "Bitcoin", "Nasdaq", "FOMC", "CPI", "Fed"])
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except (json.JSONDecodeError, TypeError):
            keywords = [keywords]
    keyword_str = " ".join(keywords[:6]) if keywords else "BTC Bitcoin"

    try:
        result = safe_call_tool(client, conn, "mcp/news", "search_news", {
            "keyword": keyword_str,
            "limit": 20,
        })
    except Exception as exc:
        logger.warning("Gate News sync failed: %s", exc)
        return {"synced": False, "error": str(exc)}

    return {"synced": True, "source": "gate_mcp_news", "keywords": keyword_str}

def sync_gate_square_hot(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate MCP /mcp/info 拉取 Square 热门帖子。"""
    if client is None:
        client = _default_client(conn)

    limit = 20
    errors: list[str] = []
    result: dict[str, Any] | None = None
    used_endpoint = ""
    used_tool = ""
    candidate_results: list[dict[str, Any]] = []
    for endpoint, tool_name, arguments in _square_hot_tool_candidates(client, limit):
        try:
            result = safe_call_tool(client, conn, endpoint, tool_name, arguments)
            used_endpoint = endpoint
            used_tool = tool_name
        except Exception as exc:
            errors.append(f"{endpoint}/{tool_name}: {exc}")
            continue
        data = parse_json_from_content(result)
        now = utc_now()
        raw_posts = _normalize_square_posts(data, now)
        if raw_posts:
            candidate_results.append({
                "endpoint": endpoint,
                "tool": tool_name,
                "raw_posts": raw_posts,
                "result": result,
            })
            break
        errors.append(f"{endpoint}/{tool_name}: empty result after normalization")
    if not candidate_results:
        error_message = "; ".join(errors) if errors else "no square tool candidate available"
        logger.warning("Gate Square sync failed: %s", error_message)
        return {"synced": False, "error": error_message}

    best = candidate_results[0]
    used_endpoint = best["endpoint"]
    used_tool = best["tool"]
    raw_posts = best["raw_posts"]
    posts = list(raw_posts)
    posts.sort(key=_square_hot_rank, reverse=True)
    posts = posts[:limit]
    posts, translated_count = _translate_posts_to_chinese(posts)

    # 清除旧的热门帖子数据，只保留本次同步的最新热度帖子
    try:
        conn.execute("DELETE FROM gate_square_posts WHERE is_hot_post = 1")
        conn.commit()
    except Exception:
        pass

    synced = 0

    for post in posts:
        try:
            conn.execute(
                """
                INSERT INTO gate_square_posts (
                    post_id, author, author_id, content, publish_time,
                    likes, comments, repost_count,
                    sentiment, tags, source, fetched_at, created_at,
                    hot_score, is_followed_user, is_hot_post
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 1)
                ON CONFLICT(post_id) DO UPDATE SET
                    content = excluded.content,
                    source = excluded.source,
                    likes = excluded.likes,
                    comments = excluded.comments,
                    repost_count = excluded.repost_count,
                    hot_score = excluded.hot_score,
                    fetched_at = excluded.fetched_at,
                    is_hot_post = 1
                """,
                (
                    post["post_id"],
                    post["author"],
                    post["author_id"],
                    post["content"],
                    post["publish_time"],
                    post["likes"],
                    post["comments"],
                    post["repost_count"],
                    None,
                    json.dumps(post["tags"], ensure_ascii=False),
                    post["source"],
                    now,
                    now,
                    post["hot_score"],
                ),
            )
            synced += 1
        except Exception:
            continue
    conn.commit()
    return {
        "synced": True,
        "count": synced,
        "raw_candidate_count": len(raw_posts),
        "translated_count": translated_count,
        "source": used_endpoint,
        "tool": used_tool,
        "preview": _square_preview(posts or raw_posts),
        "candidates_failed": errors,
    }

def sync_gate_square_user_opinions(conn: sqlite3.Connection, client: GateMCPClient | None = None) -> dict[str, Any]:
    """从 Gate Square 读取指定用户的新帖子，作为分析师观点进入解析管线。"""
    from .services import create_opinion, get_or_create_analyst, get_setting_value

    if client is None:
        client = _default_client(conn)

    followed_users = get_setting_value(conn, "square.followed_users", [])
    if isinstance(followed_users, str):
        try:
            followed_users = json.loads(followed_users)
        except (json.JSONDecodeError, TypeError):
            followed_users = []
    if not followed_users:
        return {"synced": False, "reason": "no followed users configured"}

    total_synced = 0
    total_opinions = 0
    total_reviews = 0
    now = utc_now()

    for user_cfg in followed_users:
        if not isinstance(user_cfg, dict):
            continue
        source_user_id = str(user_cfg.get("source_user_id") or "")
        display_name = str(user_cfg.get("display_name") or source_user_id)
        if not source_user_id:
            continue

        analyst = get_or_create_analyst(conn, display_name, "gate_square_user")
        conn.execute(
            """
            INSERT INTO analyst_source_accounts (
                analyst_id, source_platform, source_user_id, display_name, enabled, created_at
            ) VALUES (?, 'gate_square', ?, ?, 1, ?)
            ON CONFLICT(source_platform, source_user_id) DO UPDATE SET
                analyst_id = excluded.analyst_id,
                display_name = excluded.display_name,
                enabled = 1
            """,
            (analyst["id"], source_user_id, display_name, now),
        )
        conn.commit()

        # 拉取该用户的帖子
        try:
            result = safe_call_tool(client, conn, "mcp", "get_square_user_posts", {
                "user_id": source_user_id,
                "limit": 10,
            })
        except Exception as exc:
            logger.warning("Gate Square user sync failed for %s: %s", source_user_id, exc)
            continue

        data = parse_json_from_content(result)
        posts = data if isinstance(data, list) else []

        for post in posts:
            if not isinstance(post, dict):
                continue
            post_id = str(post.get("id") or post.get("post_id") or "")
            if not post_id:
                continue
            content = str(post.get("content") or post.get("text") or "")
            if not content or len(content.strip()) < 10:
                continue

            # 写入 gate_square_posts（标记为关注用户）
            try:
                conn.execute(
                    """
                    INSERT INTO gate_square_posts (
                        post_id, author, author_id, content, publish_time,
                        likes, comments, repost_count, sentiment, tags,
                        source, fetched_at, created_at,
                        is_followed_user, is_hot_post, hot_score
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, 0)
                    ON CONFLICT(post_id) DO UPDATE SET
                        likes = excluded.likes,
                        comments = excluded.comments,
                        fetched_at = excluded.fetched_at,
                        is_followed_user = 1
                    """,
                    (
                        post_id,
                        display_name,
                        source_user_id,
                        content[:4000],
                        post.get("publish_time") or post.get("created_at"),
                        int(post.get("likes") or post.get("like_count") or 0),
                        int(post.get("comments") or post.get("comment_count") or 0),
                        int(post.get("reposts") or post.get("repost_count") or 0),
                        None,
                        json.dumps(post.get("tags") or [], ensure_ascii=False),
                        "gate_square_user",
                        now,
                        now,
                    ),
                )
                total_synced += 1
            except Exception:
                continue

            source_url = f"gate_square://{source_user_id}/{post_id}"
            already = conn.execute(
                "SELECT id FROM raw_opinions WHERE source_url = ?",
                (source_url,),
            ).fetchone()
            if already:
                continue
            pending_review = conn.execute(
                "SELECT id FROM opinion_review_drafts WHERE source_url = ?",
                (source_url,),
            ).fetchone()
            if pending_review:
                continue
            payload = SimpleNamespace(
                analyst_name=display_name,
                content=content[:4000],
                source_url=source_url,
                published_at=post.get("publish_time") or post.get("created_at") or now,
            )
            try:
                result = create_opinion(conn, payload)
                total_opinions += len(result.get("prediction_ids") or [])
                if result.get("needs_user_confirmation"):
                    total_reviews += 1
            except Exception as exc:
                logger.warning("Gate Square opinion ingestion failed for %s: %s", source_url, exc)
                continue

        conn.commit()

    return {"synced": True, "posts": total_synced, "predictions_created": total_opinions, "reviews_created": total_reviews}
