import json
import sys

def parse_int(val, default=0):
    try:
        if isinstance(val, (int, float)):
            return int(val)
        if isinstance(val, str):
            clean = val.strip().strip('"').strip("'")
            return int(clean)
    except Exception:
        pass
    return default

def parse_bool(val, default=False):
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("true", "1", "yes")
    return default

def main():
    try:
        input_data = sys.stdin.read()
        if not input_data:
            print(json.dumps({"decision": "allow"}))
            return

        payload = json.loads(input_data)
        tool_call = payload.get("toolCall", {})
        args = tool_call.get("args", {})

        is_daemon = parse_bool(args.get("IsDaemon", False))
        wait_ms = parse_int(args.get("WaitMsBeforeAsync", 0))

        overwrites = {}
        # For non-daemon tasks: if sync wait time is below 10000ms (system ceiling),
        # elevate it to 10000ms to avoid premature background task degradation.
        if not is_daemon and wait_ms < 10000:
            overwrites["WaitMsBeforeAsync"] = 10000

        resp = {"decision": "allow"}
        if overwrites:
            resp["overwrite"] = overwrites

        print(json.dumps(resp))
    except Exception:
        # Fallback to allow execution on unexpected errors
        print(json.dumps({"decision": "allow"}))

if __name__ == "__main__":
    main()
