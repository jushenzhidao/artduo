#!/usr/bin/env python3
"""积分账本 + new-api 令牌额度桥接。

为什么需要这一层（第一性原理）：
  - new-api 的 sk- key 自带额度（token.remain_quota / used_quota），请求成功即扣费；
    页面上的「积分」如果只是 JS 里的一个数字，刷新就回到 2000，和真实账单毫无关系。
  - 但 new-api 只对**它自己转发的请求**记账；本代理还要做参考图校验、重试、并发闸门、
    免费档视频，这些都不该重复计费。于是把「计费」收敛成两件事：
      1) 读上游：用当前 sk- key 调 /v1/dashboard/billing/*，拿到总额/已用（只读，不计费）；
      2) 本地账本：提交时冻结、成功按实际结算、失败/取消释放，并把每一笔写进 sqlite。
  - 两者关系：上游额度是「资金来源」，本地账本是「本次会话的明细与冻结」。
    上游不支持 billing 端点时（例如 bfl2openai 这类只做生成的网关），自动退化为
    本地账本（余额持久化，重启不丢），绝不假装读到了上游余额。

口径（全部可用环境变量覆盖）：
  NEWAPI_QUOTA_PER_USD   默认 500000，new-api 默认 1 美元 = 500000 quota
  JIMENG_CREDITS_PER_USD 默认 100，即 1 积分 = 0.01 美元 = 5000 quota
  JIMENG_BILLING_UNIT_USD 默认 1.0；站点额度展示为 CNY 时按汇率设 0.14 之类
  JIMENG_START_CREDITS   默认 2000，仅本地账本模式使用
  JIMENG_BILLING_DB      默认 qa/billing.db

注意 new-api 的 /v1/dashboard/billing/subscription 返回的 hard_limit_usd 是
「剩余 + 已用」的总额度，不是剩余；剩余必须再减 /usage 的 total_usage（单位：美分）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
QA = ROOT / "qa"
DB_PATH = Path(os.environ.get("JIMENG_BILLING_DB", str(QA / "billing.db")))
QUOTA_PER_USD = float(os.environ.get("NEWAPI_QUOTA_PER_USD", "500000") or 500000)
CREDITS_PER_USD = float(os.environ.get("JIMENG_CREDITS_PER_USD", "100") or 100)
BILLING_UNIT_USD = float(os.environ.get("JIMENG_BILLING_UNIT_USD", "1") or 1)
START_CREDITS = float(os.environ.get("JIMENG_START_CREDITS", "2000") or 2000)
CACHE_TTL = float(os.environ.get("JIMENG_BILLING_TTL", "5") or 5)
FAIL_TTL = float(os.environ.get("JIMENG_BILLING_FAIL_TTL", "60") or 60)
UNLIMITED_USD = 1e7          # new-api 对无限额度令牌返回 1e8
TIMEOUT = float(os.environ.get("JIMENG_BILLING_TIMEOUT", "8") or 8)
PRICE_TTL = float(os.environ.get("JIMENG_PRICE_TTL", "300") or 300)
# 老账本没有单位标记：按本代码历史的默认 1000 积分/美元回填，迁移才不会漏。
LEGACY_CREDITS_PER_USD = float(os.environ.get("JIMENG_LEGACY_CREDITS_PER_USD", "1000") or 1000)


def _num(value) -> float | None:
    """把上游可能给字符串/整数的金额收敛成 float；非法值返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def quota_to_credits(quota: float) -> float:
    return round(float(quota) / QUOTA_PER_USD * CREDITS_PER_USD, 2)


def usd_to_credits(usd: float) -> float:
    return round(float(usd) * CREDITS_PER_USD, 2)


def credits_to_usd(credits: float) -> float:
    return round(float(credits) / CREDITS_PER_USD, 6)


class BillingError(Exception):
    pass


# ---------------- 上游额度读取 ----------------

_CACHE: dict[str, dict] = {}
_CACHE_LOCK = threading.Lock()


def _cache_key(base: str, key: str) -> str:
    import hashlib
    return f"{base}|{hashlib.sha256(key.encode()).hexdigest()[:12]}"


def _http_json(url: str, key: str, timeout: float) -> tuple[int, object]:
    headers = {"Accept": "application/json",
               "User-Agent": "jimeng-clone-billing/1.0"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    req = urllib.request.Request(url, headers=headers)
    # 禁用环境代理：带 Authorization 的请求在代理下会被静默吞掉
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read()
        status = resp.status
    try:
        return status, json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise BillingError(f"非 JSON 响应（HTTP {status}）") from exc


def _http_json_retry(url: str, key: str, timeout: float,
                     attempts: int = 2) -> tuple[int, object]:
    """带一次重试的 GET。

    为什么需要：new-api 的 billing 端点是现算用户额度，实测偶发 3~25s 抖动；
    单次超时就直接判「不支持」，页面会莫名其妙退化成本地账本。
    """
    last: Exception | None = None
    for i in range(max(1, attempts)):
        try:
            return _http_json(url, key, timeout)
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code >= 500 and i + 1 < attempts:
                time.sleep(0.4)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if i + 1 < attempts:
                time.sleep(0.4)
                continue
            raise
    raise last if last else BillingError("未知网络错误")


def _unsupported(base: str, reason: str, **extra) -> dict:
    return {"supported": False, "base": base, "reason": reason,
            "total": None, "used": None, "remaining": None,
            "totalCredits": None, "usedCredits": None, "remainingCredits": None,
            "unlimited": False, **extra}


def upstream_balance(base: str, key: str, force: bool = False,
                     timeout: float | None = None) -> dict:
    """只读探测当前 sk- key 的额度；结果按 key 缓存，失败也有短缓存。

    返回字段一律是「积分」口径，另带 billing 原始单位值：
      total / used / remaining  —— 站点额度单位（USD 或 CNY，取决于站点设置）
      totalCredits / usedCredits / remainingCredits —— 换算后的积分
    """
    base = (base or "").rstrip("/")
    if not base or not key:
        return _unsupported(base, "未配置上游 Base 或 Key")
    ck = _cache_key(base, key)
    now = time.time()
    with _CACHE_LOCK:
        hit = _CACHE.get(ck)
    if hit and not force and now - hit["ts"] < hit["ttl"]:
        return dict(hit["value"], cached=True)

    value: dict
    ttl = CACHE_TTL
    try:
        _, sub = _http_json_retry(base + "/v1/dashboard/billing/subscription", key, timeout or TIMEOUT)
        if not isinstance(sub, dict):
            raise BillingError("subscription 响应不是对象")
        if sub.get("error"):
            err = sub.get("error")
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise BillingError(f"subscription 返回错误：{msg}")
        hard = _num(sub.get("hard_limit_usd"))
        if hard is None:
            hard = _num(sub.get("soft_limit_usd"))
        if hard is None:
            raise BillingError("subscription 缺少 hard_limit_usd")
        _, usage = _http_json_retry(base + "/v1/dashboard/billing/usage", key, timeout or TIMEOUT)
        if not isinstance(usage, dict):
            raise BillingError("usage 响应不是对象")
        if usage.get("error"):
            err = usage.get("error")
            msg = err.get("message") if isinstance(err, dict) else str(err)
            raise BillingError(f"usage 返回错误：{msg}")
        cents = _num(usage.get("total_usage"))
        if cents is None:
            cents = _num(usage.get("totalUsage"))
        if cents is None:
            raise BillingError("usage 缺少 total_usage")
        used = cents / 100.0
        unlimited = hard >= UNLIMITED_USD
        # hard_limit_usd 是「剩余 + 已用」的总额度，不是剩余
        remaining = None if unlimited else max(0.0, round(hard - used, 8))
        usd_total = hard * BILLING_UNIT_USD
        usd_used = used * BILLING_UNIT_USD
        usd_remaining = None if remaining is None else remaining * BILLING_UNIT_USD
        value = {
            "supported": True, "base": base, "reason": None,
            "unitUsd": BILLING_UNIT_USD,
            "total": round(hard, 6), "used": round(used, 6),
            "remaining": None if remaining is None else round(remaining, 6),
            "totalCredits": None if unlimited else usd_to_credits(usd_total),
            "usedCredits": usd_to_credits(usd_used),
            "remainingCredits": None if usd_remaining is None else usd_to_credits(usd_remaining),
            "unlimited": unlimited,
            "quotaPerUsd": QUOTA_PER_USD, "creditsPerUsd": CREDITS_PER_USD,
            "accessUntil": sub.get("access_until"),
        }
    except urllib.error.HTTPError as exc:
        # 401：key 无效；404：该网关没有 new-api 的 billing 端点（bfl2openai 就是这种）
        if exc.code in (401, 403):
            value, ttl = _unsupported(base, "上游不认这个 Key（401/403）"), FAIL_TTL
        elif exc.code == 404:
            value, ttl = _unsupported(base, "上游没有 new-api billing 端点（404）"), FAIL_TTL
        else:
            value, ttl = _unsupported(base, f"上游 billing 返回 HTTP {exc.code}"), FAIL_TTL
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        value, ttl = _unsupported(base, f"上游不可达：{type(exc).__name__}"), FAIL_TTL
    except BillingError as exc:
        value, ttl = _unsupported(base, str(exc)[:160]), FAIL_TTL
    except Exception as exc:  # 防御：任何解析意外都不能把页面打挂
        value, ttl = _unsupported(base, f"解析失败：{type(exc).__name__}"), FAIL_TTL

    value["ts"] = now
    with _CACHE_LOCK:
        _CACHE[ck] = {"value": value, "ts": now, "ttl": ttl}
    return dict(value, cached=False)


_PRICE_CACHE: dict[str, dict] = {}


def _price_of_item(item: dict, group_ratio: float) -> tuple[float | None, str]:
    """从定价表条目里取出「一次调用多少美元」，同时说明依据。

    new-api 的两种按次口径：
      - billing_mode=tiered_expr + billing_expr=tier("base", fixed(0.2))
      - quota_type=1 + model_price=0.0164
    按量计费（quota_type=0）的模型没法折算成「一张图多少钱」，返回 None。
    """
    mode = str(item.get("billing_mode") or "")
    expr = item.get("billing_expr") or ""
    if mode == "tiered_expr" and expr:
        m = re.search(r"fixed\(\s*([0-9]+(?:\.[0-9]+)?)\s*\)", expr)
        if m:
            return float(m.group(1)) * group_ratio, "tiered_expr"
        return None, "表达式计费但解析不出 fixed()"
    if int(item.get("quota_type") or 0) == 1:
        price = _num(item.get("model_price"))
        if price is None:
            return None, "按次计费但缺少 model_price"
        return price * group_ratio, "per_call"
    return None, "按量计费（非按次），无法折算单张价"


def upstream_prices(base: str, models: list[str], group: str = "default",
                    force: bool = False, timeout: float | None = None) -> dict:
    """一次拉定价表，算出多个模型的「一次调用多少积分」。

    为什么要批量：多模型下发时逐个请求会把 /api/pricing 打 N 次。这里一次拉表、
    逐条解析，缓存仍按 base|model|group 存，单模型查询命中同一份缓存。
    解析不出来就 supported=False + reason，由调用方回退默认值，绝不猜。
    """
    base = (base or "").rstrip("/")
    models = [m for m in (models or []) if isinstance(m, str) and m]
    out: dict[str, dict] = {}
    if not base or not models:
        for m in models:
            out[m] = {"supported": False, "model": m, "base": base, "usd": None,
                      "credits": None, "source": None, "reason": "未配置 Base",
                      "groupRatio": None, "asOf": None}
        return out

    now = time.time()
    missing: list[str] = []
    for m in models:
        with _CACHE_LOCK:
            hit = _PRICE_CACHE.get(f"{base}|{m}|{group}")
        if hit and not force and now - hit["ts"] < PRICE_TTL:
            out[m] = dict(hit["value"], cached=True)
        else:
            missing.append(m)
    if not missing:
        return out

    try:
        _, data = _http_json(base + "/api/pricing", "", timeout or TIMEOUT)
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list):
            raise BillingError("pricing 响应缺少 data[]")
        ratios = data.get("group_ratio") if isinstance(data.get("group_ratio"), dict) else {}
        gr = _num(ratios.get(group)) or 1.0
        by_name = {m.get("model_name"): m for m in items if isinstance(m, dict)}
        for m in missing:
            info = {"supported": False, "model": m, "base": base, "usd": None,
                    "credits": None, "source": None, "reason": None,
                    "groupRatio": gr, "asOf": None}
            item = by_name.get(m)
            if item is None:
                info["reason"] = f"定价表里没有模型 {m}"
            else:
                usd, why = _price_of_item(item, gr)
                if usd is None:
                    info["reason"] = why
                else:
                    info.update(supported=True, usd=round(usd, 6),
                                credits=usd_to_credits(usd), source=why,
                                asOf=item.get("pricing_version") or data.get("pricing_version"))
            out[m] = info
            with _CACHE_LOCK:
                _PRICE_CACHE[f"{base}|{m}|{group}"] = {"value": info, "ts": now}
        return out
    except urllib.error.HTTPError as exc:
        msg = "上游没有定价接口（404）" if exc.code == 404 else f"定价接口 HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        msg = f"定价接口不可达：{type(exc).__name__}"
    except BillingError as exc:
        msg = str(exc)[:160]
    except Exception as exc:  # 防御：定价解析失败不能把计费打挂
        msg = f"定价解析失败：{type(exc).__name__}"
    for m in missing:
        out[m] = {"supported": False, "model": m, "base": base, "usd": None,
                  "credits": None, "source": None, "reason": msg,
                  "groupRatio": None, "asOf": None}
    return out


def upstream_price(base: str, model: str, group: str = "default",
                   force: bool = False, timeout: float | None = None) -> dict:
    """单模型查询：/api/pricing 的薄封装，行为与上游定价表一致。"""
    got = upstream_prices(base, [model], group, force, timeout).get(model)
    return got or {"supported": False, "model": model, "base": base, "usd": None,
                   "credits": None, "source": None, "reason": "未知",
                   "groupRatio": None, "asOf": None}


def clear_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
        _PRICE_CACHE.clear()


# ---------------- 本地账本（sqlite） ----------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS holds (
  task_id TEXT PRIMARY KEY,
  credits REAL NOT NULL,
  status TEXT NOT NULL,           -- held | settled | released
  created REAL NOT NULL,
  updated REAL NOT NULL,
  settled REAL,
  note TEXT
);
CREATE TABLE IF NOT EXISTS entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL,
  kind TEXT NOT NULL,             -- hold | settle | release | stale
  task_id TEXT,
  credits REAL NOT NULL,
  balance_after REAL,
  note TEXT
);
CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(ts DESC);
"""


class Ledger:
    """一个进程内可多线程使用的 sqlite 账本；每次操作独立连接，事务用 BEGIN IMMEDIATE。"""

    def __init__(self, path: str | Path = DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._conn()
        try:
            conn.executescript(SCHEMA)
            conn.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('balance', ?)",
                         (str(START_CREDITS),))
            conn.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('created', ?)",
                         (str(time.time()),))
            self._migrate_unit(conn)
        finally:
            conn.close()
        try:
            os.chmod(self.path, 0o600)      # 账本含消费明细，只给本用户读
        except OSError:
            pass

    @staticmethod
    def _migrate_unit(conn) -> None:
        """比例变了就把历史积分按新单位重算，保持美元价值不变。

        积分只是美元的显示单位：1 USD=1000 积分改成 100 之后，历史流水里的 38 分
        必须变成 3.8 分，否则同一笔消费的美元价值会被凭空放大 10 倍。
        """
        row = conn.execute("SELECT v FROM meta WHERE k='credits_per_usd'").fetchone()
        try:
            old = float(row["v"]) if row else None
        except (TypeError, ValueError):
            old = None
        if old is None:
            # 老库没有单位标记：有流水就按历史默认 1000 回填，否则就是全新账本。
            has_rows = bool(conn.execute("SELECT 1 FROM entries LIMIT 1").fetchone()
                            or conn.execute("SELECT 1 FROM holds LIMIT 1").fetchone())
            old = LEGACY_CREDITS_PER_USD if has_rows else CREDITS_PER_USD
            if not has_rows:
                conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('credits_per_usd', ?)",
                             (str(CREDITS_PER_USD),))
                return
        if old <= 0 or abs(old - CREDITS_PER_USD) < 1e-9:
            return
        scale = CREDITS_PER_USD / old
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE meta SET v=ROUND(CAST(v AS REAL)*?, 2) WHERE k='balance'",
                         (scale,))
            conn.execute("UPDATE holds SET credits=ROUND(credits*?, 2), "
                         "settled=ROUND(COALESCE(settled, 0)*?, 2)", (scale, scale))
            conn.execute("UPDATE entries SET credits=ROUND(credits*?, 2), "
                         "balance_after=ROUND(COALESCE(balance_after, 0)*?, 2)", (scale, scale))
            conn.execute("INSERT OR REPLACE INTO meta(k, v) VALUES('credits_per_usd', ?)",
                         (str(CREDITS_PER_USD),))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @staticmethod
    def _entry(conn, kind: str, task_id: str | None, credits: float,
               balance: float | None, note: str | None) -> None:
        conn.execute(
            "INSERT INTO entries(ts, kind, task_id, credits, balance_after, note) "
            "VALUES(?,?,?,?,?,?)",
            (time.time(), kind, task_id, round(float(credits), 2),
             None if balance is None else round(float(balance), 2), note))

    @staticmethod
    def _balance_conn(conn) -> float:
        row = conn.execute("SELECT v FROM meta WHERE k='balance'").fetchone()
        return round(float(row["v"]), 2) if row else float(START_CREDITS)

    def balance(self) -> float:
        conn = self._conn()
        try:
            return self._balance_conn(conn)
        finally:
            conn.close()

    def _set_balance(self, conn, value: float) -> None:
        conn.execute("UPDATE meta SET v=? WHERE k='balance'", (str(round(value, 2)),))

    def hold(self, task_id: str, credits: float, base: float, note: str | None = None) -> dict:
        """在 base（不含冻结的可用额度）内冻结 credits；同一 task_id 幂等。

        返回 {"ok": bool, "held": 当前冻结总额, "existing": 是否已存在}
        """
        credits = round(float(credits), 2)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM holds WHERE task_id=?", (task_id,)).fetchone()
            if row:
                conn.execute("COMMIT")
                return {"ok": True, "existing": True, "status": row["status"],
                        "held": self._held(conn), "credits": row["credits"]}
            held = self._held(conn)
            if credits > max(0.0, float(base) - held) + 1e-9:
                conn.execute("ROLLBACK")
                return {"ok": False, "existing": False, "held": held, "base": float(base),
                        "need": credits, "available": round(max(0.0, float(base) - held), 2)}
            now = time.time()
            conn.execute(
                "INSERT INTO holds(task_id, credits, status, created, updated, note) "
                "VALUES(?,?,?,?,?,?)", (task_id, credits, "held", now, now, note))
            self._entry(conn, "hold", task_id, credits, self._balance_conn(conn), note or "冻结")
            conn.execute("COMMIT")
            return {"ok": True, "existing": False, "held": round(held + credits, 2),
                    "credits": credits}
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def settle(self, task_id: str, credits: float, mode: str = "upstream",
               note: str | None = None) -> bool:
        """把冻结转成实际扣费；本地账本模式下同步扣余额。重复结算幂等。"""
        credits = round(float(credits), 2)
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM holds WHERE task_id=?", (task_id,)).fetchone()
            if row and row["status"] != "held":
                conn.execute("COMMIT")
                return False
            now = time.time()
            if not row:
                conn.execute(
                    "INSERT INTO holds(task_id, credits, status, created, updated, settled, note) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (task_id, credits, "settled", now, now, credits, note or "补记"))
            else:
                conn.execute(
                    "UPDATE holds SET status='settled', settled=?, updated=?, note=? "
                    "WHERE task_id=?", (credits, now, note or row["note"], task_id))
            balance = self._balance_conn(conn)
            if mode == "local":
                balance = round(max(0.0, balance - credits), 2)
                self._set_balance(conn, balance)
            self._entry(conn, "settle", task_id, credits, balance, note or "结算")
            conn.execute("COMMIT")
            return True
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def release(self, task_id: str, note: str | None = None) -> bool:
        """释放未结算的冻结；已结算的不动（避免把已经花掉的钱退回来）。"""
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM holds WHERE task_id=?", (task_id,)).fetchone()
            if not row:
                conn.execute("COMMIT")
                return False
            if row["status"] != "held":
                conn.execute("COMMIT")
                return False
            conn.execute(
                "UPDATE holds SET status='released', updated=?, note=? WHERE task_id=?",
                (time.time(), note or row["note"], task_id))
            self._entry(conn, "release", task_id, row["credits"], self._balance_conn(conn),
                        note or "释放冻结")
            conn.execute("COMMIT")
            return True
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def release_stale(self, max_age: float = 1800) -> int:
        """进程重启/崩溃后清理孤儿冻结：超过 max_age 秒仍未结算的 held 一律释放。"""
        cutoff = time.time() - max_age
        conn = self._conn()
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT task_id, credits FROM holds WHERE status='held' AND created < ?",
                (cutoff,)).fetchall()
            for r in rows:
                conn.execute(
                    "UPDATE holds SET status='released', updated=?, note=? WHERE task_id=?",
                    (time.time(), "启动清理孤儿冻结", r["task_id"]))
                self._entry(conn, "stale", r["task_id"], r["credits"], self._balance_conn(conn),
                            "启动清理孤儿冻结")
            conn.execute("COMMIT")
            return len(rows)
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        finally:
            conn.close()

    def _held(self, conn) -> float:
        row = conn.execute(
            "SELECT COALESCE(SUM(credits), 0) AS s FROM holds WHERE status='held'").fetchone()
        return round(float(row["s"]), 2)

    def held_total(self) -> float:
        conn = self._conn()
        try:
            return self._held(conn)
        finally:
            conn.close()

    def today_spent(self) -> float:
        now = datetime.now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COALESCE(SUM(credits),0) AS s FROM entries "
                "WHERE kind='settle' AND ts>=?", (midnight,)).fetchone()
            return round(float(row["s"]), 2)
        finally:
            conn.close()

    def entries(self, limit: int = 30) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT id, ts, kind, task_id, credits, balance_after, note "
                "FROM entries ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def stats(self) -> dict:
        conn = self._conn()
        try:
            by = {r["status"]: round(float(r["n"]), 2) for r in conn.execute(
                "SELECT status, SUM(credits) AS n FROM holds GROUP BY status").fetchall()}
            total = conn.execute("SELECT COUNT(*) AS n FROM holds").fetchone()["n"]
            balance = self._balance_conn(conn)
            held = self._held(conn)
        finally:
            conn.close()
        return {"held": held, "byStatus": by, "holds": total,
                "balance": balance, "todaySpent": self.today_spent()}


__all__ = ["Ledger", "upstream_balance", "upstream_price", "clear_cache",
           "quota_to_credits", "usd_to_credits", "credits_to_usd",
           "QUOTA_PER_USD", "CREDITS_PER_USD"]
