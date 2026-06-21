"""See the compression happen.

    python examples/demo.py
"""

import json

from headroom_ja import compress, retrieve
from headroom_ja.tokens import BACKEND


def make_orders(n=500):
    data = [{"id": i, "user": "kim", "amount": 12000, "status": "支払済"}
            for i in range(n)]
    data[400]["amount"] = 980000
    data[499] = {"id": 499, "user": "choi", "amount": 8000,
                 "status": "エラー", "msg": "カードが拒否されました"}
    return data


def main():
    print(f"token backend: {BACKEND}\n")
    content = json.dumps(make_orders(), ensure_ascii=False)

    r = compress(content, query="拒否された注文はある？")
    print(r)
    print("\n--- compressed output (head) ---")
    print(r.text[:400], "...")

    if r.cache_key:
        print("\n--- retrieve('拒否') ---")
        for h in retrieve(r.cache_key, query="拒否"):
            print(h)


if __name__ == "__main__":
    main()
