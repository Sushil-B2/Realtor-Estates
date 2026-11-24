#!/usr/bin/env python3
"""
Automated `sa11y`-style WCAG 2.2 AA Accessibility CI Gate (`v1.39.0`, `docs/50`).
Audits `web/index.html`, `/site`, and published SSR funnel pages (`/f/<slug>`).
Exits non-zero on any WCAG 2.2 AA violation.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("RE_DB", "json")

from app import api, enterprise_observability_sso as eos_mod, server as srv_mod  # noqa: E402
from app.store import Store  # noqa: E402


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        store = Store(os.path.join(tmp, "a11y_store.json"))
        tid = store.db["tenants"][0]["id"]
        orig_store = srv_mod.STORE
        srv_mod.STORE = store
        try:
            dummy = srv_mod.Handler.__new__(srv_mod.Handler)
            site_html = dummy._public_site()
        finally:
            srv_mod.STORE = orig_store

        res = eos_mod.audit_workspace_accessibility(
            store,
            tid,
            public_funnel_renderer=api.public_funnel_page,
            public_site_html=site_html,
        )
        if "--json" in sys.argv:
            print(json.dumps(res, indent=2))
        else:
            print(
                f"check_a11y: audited {res['pages_audited']} surface(s) against {res['wcag_standard']} — "
                f"{res['total_violations']} violation(s)"
            )
            for rep in res["reports"]:
                status = "PASS" if rep["passed"] else "FAIL"
                print(f"  [{status}] {rep['page']} (contrast {rep['brand_contrast_ratio']}:1, score {rep['score']}/100)")
                for v in rep["violations"]:
                    print(f"         - [{v['severity']}] {v['rule']} (WCAG {v['wcag']}): {v['detail']}")
        return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
