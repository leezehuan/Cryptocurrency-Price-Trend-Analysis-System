from backend.app.database import connect
import json

conn = connect()

# 诊断 Gate MCP Square 同步原始记录
print("=== Gate MCP Square 原始记录 ===")
rows = conn.execute(
    "SELECT endpoint, tool_name, status, request_payload, response_payload, latency_ms, created_at "
    "FROM gate_mcp_raw_records "
    "WHERE tool_name = 'cex_square_list_square_ai_search' OR tool_name = 'get_square_hot' "
    "ORDER BY created_at DESC LIMIT 5"
).fetchall()
for r in rows:
    print(f"--- {r['created_at']} | {r['endpoint']}/{r['tool_name']} | status={r['status']} | latency={r['latency_ms']}ms")
    req = json.loads(r["request_payload"]) if r["request_payload"] else {}
    print(f"request: {req}")
    res = r["response_payload"]
    try:
        data = json.loads(res) if isinstance(res, str) else res
        print(f"result type: {type(data)}")
        if isinstance(data, dict):
            print(f"keys: {list(data.keys())}")
            if "content" in data:
                content = data["content"]
                if isinstance(content, list) and content:
                    first = content[0]
                    print(f"content[0] type: {type(first)}")
                    if isinstance(first, dict):
                        print(f"content[0] keys: {list(first.keys())}")
                        text = first.get("text", "")
                        print(f"content[0].text length: {len(text)}")
                        # Try parse nested json in text
                        if text:
                            try:
                                nested = json.loads(text)
                                print(f"nested type: {type(nested)}")
                                if isinstance(nested, dict):
                                    if "data" in nested:
                                        d = nested["data"]
                                        print(f"nested.data type: {type(d)}")
                                        if isinstance(d, dict):
                                            for k in ("items", "posts", "list"):
                                                if k in d:
                                                    print(f"nested.data.{k} type: {type(d[k])}, len: {len(d[k]) if isinstance(d[k], (list, dict)) else 'N/A'}")
                                                    break
                                        elif isinstance(d, list):
                                            print(f"nested.data is list, len: {len(d)}")
                                elif isinstance(nested, list):
                                    print(f"nested is list, len: {len(nested)}")
                            except Exception as ne:
                                print(f"nested parse error: {ne}")
                                print(f"text preview: {text[:200]}")
        elif isinstance(data, list):
            print(f"list length: {len(data)}")
            if data:
                print(f"first item: {str(data[0])[:300]}")
        else:
            print(f"preview: {str(data)[:300]}")
    except Exception as e:
        print(f"parse error: {e}")
        print(f"raw preview: {str(res)[:300]}")
    print()

# 诊断 market_data 表
print("=== Market Data 统计 ===")
count = conn.execute("SELECT COUNT(*) AS cnt FROM market_data WHERE interval = '1h'").fetchone()
print(f"market_data 1h rows: {count['cnt']}")

mt_counts = conn.execute(
    "SELECT market_type, COUNT(*) AS cnt FROM market_data WHERE interval = '1h' GROUP BY market_type"
).fetchall()
for r in mt_counts:
    print(f"  market_type={r['market_type']}: {r['cnt']} rows")

# 检查最近数据
recent = conn.execute(
    "SELECT symbol, market_type, interval, open_time FROM market_data "
    "WHERE interval = '1h' ORDER BY open_time DESC LIMIT 1"
).fetchone()
if recent:
    print(f"最新 1h 数据: {dict(recent)}")

conn.close()
print("诊断完成")
