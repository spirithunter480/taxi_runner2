import os
import sys
import json
import asyncio
import time
import random
from pathlib import Path
from datetime import datetime, timezone
import aiohttp

# سقف زمان کل اجرا: ۵ ساعت و ۵۰ دقیقه (به ثانیه)
MAX_RUNTIME_SECONDS = (5 * 60 + 50) * 60  # 21,000 Seconds

BASE_URL = "https://thecryptotaxi.com"
BASE_DIR = Path(__file__).resolve().parent
ACCOUNTS_FILE = BASE_DIR / "accounts.json"

ACCOUNT_WAKE_EVENTS = {}
GLOBAL_FLEET_STATUS = {}
OWNER_REVOKE_DEADLINES = {}
BACKGROUND_TASKS = []

CONNECTION_LOST = False
LAST_CONNECTION_ALERT_TIME = 0

def check_connection_status(is_online):
    """مدیریت تمیز و تک‌باره هشدارهای قطعی و وصلی اینترنت/سرور"""
    global CONNECTION_LOST, LAST_CONNECTION_ALERT_TIME
    now_ts = time.time()
    now_str = datetime.now().strftime("%H:%M:%S")

    if not is_online:
        if not CONNECTION_LOST and (now_ts - LAST_CONNECTION_ALERT_TIME > 60):
            CONNECTION_LOST = True
            LAST_CONNECTION_ALERT_TIME = now_ts
            print(f"\n⚠️ [{now_str}] [CONNECTION NOTICE] Server / Internet connection lost. Pausing quietly until restored...\n", flush=True)
    else:
        if CONNECTION_LOST:
            CONNECTION_LOST = False
            print(f"\n[✔] [{now_str}] [CONNECTION RESTORED] Connection successfully re-established. Resuming operations...\n", flush=True)

def log_event(msg):
    """چاپ زنده رویدادها در کنسول بدون بافر"""
    print(msg, flush=True)

CAR_BASE_EARNINGS = {
    "1800": 0.015,
    "1830": 0.024,
    "1850": 0.040,
    "1880": 0.084,
    "1900": 0.216,
    "1930": 0.340,
    "1950": 0.568,
    "1990": 0.732,
    "2000": 1.412,
    "2010": 2.712,
    "2015": 1.300,
    "2016": 0.143,
    "2020": 4.920,
    "2025": 0.335,
    "2026": 1.290,
}

def get_car_base_profit(car_name, model_id):
    text = f"{car_name} {model_id}".lower()
    for yr, p in CAR_BASE_EARNINGS.items():
        if yr in text:
            return p
    return 0.015

def load_accounts():
    """بارگذاری اکانت‌ها از فایل accounts.json یا از Secret گیت‌هاب"""
    if ACCOUNTS_FILE.exists():
        try:
            with open(ACCOUNTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[-] Error loading accounts.json: {e}", flush=True)

    env_accs = os.getenv("ACCOUNTS_JSON")
    if env_accs:
        try:
            return json.loads(env_accs)
        except Exception as e:
            print(f"[-] Error parsing ACCOUNTS_JSON environment secret: {e}", flush=True)

    print("[-] No valid accounts data found (neither file nor ACCOUNTS_JSON secret).", flush=True)
    return []

def get_headers(acc):
    return {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "cache-control": "no-cache",
        "content-type": "application/json",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "x-ct": "1",
        "x-ct-device": acc.get("device_id", ""),
        "x-ct-lang": "en",
        "referrer": f"{BASE_URL}/",
        "user-agent": acc.get("user_agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"),
        "cookie": acc.get("cookie", "")
    }

async def safe_post(session, endpoint, payload=None, proxy=None):
    url = f"{BASE_URL}/api/{endpoint}"
    for attempt in range(4):
        try:
            async with session.post(url, json=payload or {}, proxy=proxy, timeout=aiohttp.ClientTimeout(total=25)) as resp:
                if resp.status == 429:
                    await asyncio.sleep(12)
                    continue
                return await resp.json()
        except Exception:
            if attempt == 3:
                return None
            await asyncio.sleep(3)
    return None

async def background_market_sniper(accounts):
    """اسنایپر هوشمند دیده‌بان چرخشی مارکت رنت"""
    current_scout = None
    scout_switch_time = time.time()
    session_duration = random.uniform(900.0, 1800.0)

    while True:
        try:
            now_ts = time.time()
            utc_now = datetime.now(timezone.utc)
            current_shift = "AM" if utc_now.hour < 12 else "PM"

            if current_shift == "AM":
                minutes_to_shift_end = (11 - utc_now.hour) * 60 + (60 - utc_now.minute)
            else:
                minutes_to_shift_end = (23 - utc_now.hour) * 60 + (60 - utc_now.minute)
            hours_to_shift_end = minutes_to_shift_end / 60.0

            eligible_scouts = [
                a for a in accounts
                if GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("AM", 0) < 2
                or GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("PM", 0) < 2
            ]

            if not eligible_scouts:
                await asyncio.sleep(60)
                continue

            scout_is_full = False
            if current_scout:
                c_am = GLOBAL_FLEET_STATUS.get(current_scout.get("name"), {}).get("AM", 0)
                c_pm = GLOBAL_FLEET_STATUS.get(current_scout.get("name"), {}).get("PM", 0)
                if c_am >= 2 and c_pm >= 2:
                    scout_is_full = True

            if (current_scout is None) or (now_ts - scout_switch_time > session_duration) or scout_is_full:
                available_choices = [a for a in eligible_scouts if a.get("name") != (current_scout.get("name") if current_scout else "")]
                current_scout = random.choice(available_choices) if available_choices else eligible_scouts[0]
                scout_switch_time = now_ts
                session_duration = random.uniform(900.0, 1800.0)

            scout_proxy = current_scout.get("proxy") or None

            connector = aiohttp.TCPConnector(ssl=False)
            async with aiohttp.ClientSession(headers=get_headers(current_scout), connector=connector) as session:
                market_data = await safe_post(session, "market", {"modelId": "", "shift": "", "sort": "recent"}, proxy=scout_proxy)
                listings = market_data.get("listings", []) if market_data else []

                for item in listings:
                    listing_id = str(item.get("id") or item.get("listingId"))
                    car_name = item.get("name", "Taxi")
                    model_id = str(item.get("modelId", ""))

                    raw_shift_str = str(item.get("shift", "")).strip().upper()
                    if "PM" in raw_shift_str or "AFTERNOON" in raw_shift_str:
                        shift = "PM"
                    elif "AM" in raw_shift_str or "MORNING" in raw_shift_str:
                        shift = "AM"
                    else:
                        shift = current_shift

                    raw_owner = item.get("ownerName") or item.get("owner") or item.get("username") or item.get("userName") or "Unknown"
                    owner_name = raw_owner.get("username") or raw_owner.get("name") if isinstance(raw_owner, dict) else str(raw_owner)

                    raw_trips = item.get("dailyTrips") or item.get("tripsPerDay") or item.get("trips") or item.get("quota") or 0
                    if isinstance(raw_trips, dict):
                        daily_trips = raw_trips.get("left", 0)
                    elif str(raw_trips).isdigit():
                        daily_trips = int(raw_trips)
                    else:
                        daily_trips = 0

                    owner_pct = item.get("ownerPct", 100)
                    renter_pct = item.get("myPct") or item.get("renterPct") or (100 - owner_pct)

                    c_sub = item.get("car") if isinstance(item.get("car"), dict) else {}
                    q_sub = item.get("quota") if isinstance(item.get("quota"), dict) else c_sub.get("quota", {})

                    raw_left = item.get("tripsLeft") or item.get("left") or q_sub.get("left") or c_sub.get("tripsLeft")
                    if raw_left is not None and str(raw_left).isdigit():
                        market_avail_today = int(raw_left)
                    else:
                        market_avail_today = daily_trips

                    base_p = get_car_base_profit(car_name, model_id)
                    trip_profit = base_p * (renter_pct / 100.0)
                    daily_profit = trip_profit * daily_trips
                    est_week = daily_profit * 7
                    text = f"{car_name} {model_id}".lower()

                    is_qualified = False
                    is_rescue = False

                    # رده ۱: سوپر لوکس
                    if any(k in text for k in ["2000", "2010", "2015", "2020", "2026", "halloween", "modelo x"]):
                        if renter_pct >= 10 and daily_trips >= 1:
                            is_qualified = True

                    # رده ۲: لوکس
                    elif any(k in text for k in ["1950", "1990"]):
                        if (renter_pct >= 10 and daily_trips >= 2) or (renter_pct >= 15 and daily_trips >= 1):
                            is_qualified = True

                    # رده ۳: نیمه‌لوکس
                    elif any(k in text for k in ["1900", "1930", "2025", "influencer"]):
                        if (
                            (renter_pct >= 50 and daily_trips >= 1) or
                            (renter_pct >= 40 and daily_trips >= 2) or
                            (renter_pct >= 30 and daily_trips >= 3) or
                            (renter_pct >= 20 and daily_trips >= 4) or
                            (renter_pct >= 10 and daily_trips >= 7)
                        ):
                            is_qualified = True

                    # رده ۴: اقتصادی بالا
                    elif any(k in text for k in ["1880", "2016", "cheap"]):
                        if (
                            (renter_pct >= 50 and daily_trips >= 2) or
                            (renter_pct >= 40 and daily_trips >= 3) or
                            (renter_pct >= 30 and daily_trips >= 4) or
                            (renter_pct >= 20 and daily_trips >= 6) or
                            (renter_pct >= 10 and daily_trips >= 8)
                        ):
                            is_qualified = True

                    # رده ۵: اقتصادی متوسط
                    elif "1850" in text:
                        if (
                            (renter_pct >= 70 and daily_trips >= 2) or
                            (renter_pct >= 50 and daily_trips >= 3) or
                            (renter_pct >= 40 and daily_trips >= 4) or
                            (renter_pct >= 30 and daily_trips >= 5) or
                            (renter_pct >= 20 and daily_trips >= 8)
                        ):
                            is_qualified = True

                    # رده ۶: اقتصادی پایه (۱۸۳۰)
                    elif "1830" in text:
                        if (
                            (renter_pct >= 80 and daily_trips >= 3) or
                            (renter_pct >= 60 and daily_trips >= 4) or
                            (renter_pct >= 50 and daily_trips >= 5) or
                            (renter_pct >= 30 and daily_trips >= 7)
                        ):
                            is_qualified = True

                    # رده ۷: استاندارد (۱۸۰۰)
                    elif "1800" in text:
                        if (
                            (renter_pct >= 80 and daily_trips >= 5) or
                            (renter_pct >= 50 and daily_trips >= 8)
                        ):
                            is_qualified = True
                        elif hours_to_shift_end <= 4.0 and shift == current_shift:
                            meets_rescue_terms = (
                                (renter_pct >= 50 and daily_trips >= 5) or
                                (renter_pct >= 40 and daily_trips >= 6) or
                                (renter_pct >= 30 and daily_trips >= 8)
                            )
                            if meets_rescue_terms and market_avail_today > 0:
                                valid_rescue_targets = [
                                    a for a in accounts
                                    if GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get(shift, 0) < 2
                                    and 0 < GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("rent_needed", 0) <= market_avail_today
                                    and (GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("rent_needed", 0) * 18) <= minutes_to_shift_end
                                ]
                                if valid_rescue_targets:
                                    is_qualified = True
                                    is_rescue = True

                    if is_qualified:
                        candidates = [
                            c for c in accounts
                            if GLOBAL_FLEET_STATUS.get(c.get("name"), {}).get(shift, 0) < 2
                        ]

                        if candidates:
                            random.shuffle(candidates)
                            if is_rescue:
                                candidates.sort(key=lambda a: (
                                    0 if (
                                        0 < GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("rent_needed", 0) <= market_avail_today
                                        and (GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("rent_needed", 0) * 18) <= minutes_to_shift_end
                                    ) else 1,
                                    -GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("rent_needed", 0),
                                    GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("AM", 0) + GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("PM", 0)
                                ))
                            else:
                                candidates.sort(key=lambda a: (
                                    0 if (GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("rent_needed", 0) > 0 and shift == current_shift) else 1,
                                    -GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("rent_needed", 0) if shift == current_shift else 0,
                                    GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get(shift, 0),
                                    GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("AM", 0) + GLOBAL_FLEET_STATUS.get(a.get("name"), {}).get("PM", 0)
                                ))

                            eligible_acc = candidates[0]
                            t_proxy = eligible_acc.get("proxy") or None

                            async with aiohttp.ClientSession(headers=get_headers(eligible_acc), connector=aiohttp.TCPConnector(ssl=False)) as rent_s:
                                rent_res = await safe_post(rent_s, "market/rent", {"listingId": listing_id}, proxy=t_proxy)
                                now_str = datetime.now().strftime("%H:%M:%S")

                                if rent_res and rent_res.get("contract"):
                                    c_name = eligible_acc.get("name")
                                    if c_name in GLOBAL_FLEET_STATUS:
                                        GLOBAL_FLEET_STATUS[c_name][shift] += 1

                                    total_rented = GLOBAL_FLEET_STATUS.get(c_name, {}).get("AM", 0) + GLOBAL_FLEET_STATUS.get(c_name, {}).get("PM", 0)
                                    tag_label = "RESCUE SNIPE" if is_rescue else "SNIPE SUCCESS"

                                    res_car = rent_res.get("car") if isinstance(rent_res.get("car"), dict) else {}
                                    res_contract = rent_res.get("contract") if isinstance(rent_res.get("contract"), dict) else {}
                                    res_quota = res_car.get("quota") if isinstance(res_car.get("quota"), dict) else res_contract.get("quota", {})

                                    real_left_val = None
                                    if "left" in res_quota:
                                        real_left_val = res_quota.get("left")
                                    elif "tripsLeft" in res_car:
                                        real_left_val = res_car.get("tripsLeft")
                                    elif "tripsLeft" in res_contract:
                                        real_left_val = res_contract.get("tripsLeft")

                                    if real_left_val is not None and str(real_left_val).isdigit():
                                        left_count = int(real_left_val)
                                    else:
                                        left_count = market_avail_today

                                    log_event(f"\n[{now_str}] 🎯 [{tag_label}] {c_name} rented '{car_name}' ({shift}) from @{owner_name}")
                                    log_event(f"           ↳ Trips: {daily_trips}/d ({left_count} left) | Driver: {renter_pct}% | Net: ~{trip_profit:.4f} USDT/trip | Est: ~{est_week:.2f} USDT/wk | [{total_rented}/4 Rented]\n")

                                    wake_events = globals().get("ACCOUNT_WAKE_EVENTS")
                                    if isinstance(wake_events, dict):
                                        wake_ev = wake_events.get(c_name)
                                        if wake_ev:
                                            wake_ev.set()

                                    await asyncio.sleep(2)
                                    break
                                else:
                                    log_event(f"\n[{now_str}] ⚠️ [SNIPE MISSED] '{car_name}' ({shift}) from @{owner_name} | {daily_trips} trips/d | Driver: {renter_pct}% (Already taken)\n")
        except Exception:
            pass

        await asyncio.sleep(random.uniform(1.6, 2.8))

async def background_auto_revoke_watcher(accounts):
    """نظارت هوشمند در پس‌زمینه: خواب ۲۰-۳۰ دقیقه‌ای در حالت عادی و بیدارباش دقیق در ثانیه فسخ رایگان"""
    await asyncio.sleep(20)
    next_check_ts = {}

    # پخش کردن زمان اولین بررسی اکانت‌ها
    for idx, acc in enumerate(accounts):
        next_check_ts[acc.get("name", "Account")] = time.time() + (idx * random.uniform(3.0, 7.0))

    while True:
        try:
            now_ts = time.time()
            now_ms = int(now_ts * 1000)

            for acc in accounts:
                tag = acc.get("name", "Account")
                proxy = acc.get("proxy") or None

                # بررسی اینکه آیا موعد چک دوره‌ای رسیده یا زمان فسخ یک قرارداد فرارسیده است
                known_deadline_ms = OWNER_REVOKE_DEADLINES.get(tag)
                deadline_due = (known_deadline_ms is not None) and (now_ms >= known_deadline_ms - 3000)
                periodic_due = now_ts >= next_check_ts.get(tag, 0)

                if not deadline_due and not periodic_due:
                    continue

                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(headers=get_headers(acc), connector=connector) as session:
                    st = await safe_post(session, "state", proxy=proxy)
                    if not st or not st.get("me"):
                        next_check_ts[tag] = time.time() + random.uniform(120, 240)
                        continue

                    my_id = str(st.get("me", {}).get("id", ""))
                    srv_now_ms = st.get("now", int(time.time() * 1000))

                    owner_contracts = [
                        c for c in st.get("contracts", [])
                        if (str(c.get("ownerId")) == my_id or c.get("role") == "owner")
                        and c.get("status") == "active"
                    ]

                    earliest_future_ms = None

                    for c in owner_contracts:
                        free_revoke_at = c.get("freeRevokeAt") or 0
                        can_free = bool(c.get("freeRevoke") or (free_revoke_at > 0 and srv_now_ms >= free_revoke_at))

                        if can_free:
                            contract_id = str(c.get("id"))
                            car_name = c.get("name", "Taxi")
                            shift = c.get("shift", "---")
                            tenant = c.get("renterName") or c.get("otherName") or c.get("username") or "Player"

                            payload = {"contractId": contract_id, "free": True}
                            res = await safe_post(session, "contracts/revoke", payload, proxy=proxy)
                            now_str = datetime.now().strftime("%H:%M:%S")

                            if res and (res.get("free") or res.get("contract", {}).get("status") == "revoked" or res.get("success")):
                                log_event(
                                    f"\n[{now_str}] 🚫 [AUTO-REVOKED SUCCESS] {tag} auto-revoked {shift} shift "
                                    f"from @{tenant} (Car: '{car_name}') [FREE / 0 Penalty]\n"
                                )
                                wake_ev = ACCOUNT_WAKE_EVENTS.get(tag)
                                if wake_ev:
                                    wake_ev.set()
                            else:
                                err = (res.get("error") if isinstance(res, dict) else None) or "Failed"
                                log_event(f"[{now_str}] ⚠️ [AUTO-REVOKE FAILED] {tag} ({shift} {car_name}) -> {err}")
                                if earliest_future_ms is None:
                                    earliest_future_ms = srv_now_ms + 60000
                        else:
                            if free_revoke_at > 0:
                                if earliest_future_ms is None or free_revoke_at < earliest_future_ms:
                                    earliest_future_ms = free_revoke_at

                    OWNER_REVOKE_DEADLINES[tag] = earliest_future_ms
                    idle_interval = random.uniform(1200.0, 1800.0)  # ۲۰ تا ۳۰ دقیقه

                    if earliest_future_ms is not None:
                        sec_until_revoke = max(5.0, (earliest_future_ms - srv_now_ms) / 1000.0 - 2.0)
                        next_check_ts[tag] = time.time() + min(idle_interval, sec_until_revoke)
                    else:
                        next_check_ts[tag] = time.time() + idle_interval

                await asyncio.sleep(random.uniform(1.5, 3.0))

        except asyncio.CancelledError:
            break
        except Exception:
            pass

        await asyncio.sleep(10)

async def run_taxi_for_account(acc, stagger_delay=0.0, account_index=0):
    tag = acc.get("name", "Account")
    proxy = acc.get("proxy") or None
    user_wake_event = ACCOUNT_WAKE_EVENTS.get(tag)
    last_driven_id = None
    last_mission_sync = 0
    current_rent_needed = 0
    is_deep_idle = False
    has_staggered_idle = False

    if stagger_delay > 0:
        await asyncio.sleep(stagger_delay)

    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(headers=get_headers(acc), connector=connector) as session:
        while True:
            # پاک کردن ایونت قبل از استعلام state تا هیچ سیگنال فسخ یا اسنایپی در حین اجرا از دست نرود
            if user_wake_event:
                user_wake_event.clear()

            state_data = await safe_post(session, "state", proxy=proxy)
            if not state_data or not state_data.get("me"):
                check_connection_status(is_online=False)
                await asyncio.sleep(20)
                continue

            check_connection_status(is_online=True)

            my_id = str(state_data.get("me", {}).get("id", ""))
            now_ts = time.time()
            now_ms = int(now_ts * 1000)

            # همگام‌سازی زمان نزدیک‌ترین فسخ رایگان با دیده‌بان پس‌زمینه
            earliest_revoke_ms = None
            for c in state_data.get("contracts", []):
                if c.get("status") == "active" and (str(c.get("ownerId")) == my_id or c.get("role") == "owner"):
                    if c.get("freeRevoke"):
                        earliest_revoke_ms = now_ms
                        break
                    fr_at = c.get("freeRevokeAt") or 0
                    if fr_at > 0 and (earliest_revoke_ms is None or fr_at < earliest_revoke_ms):
                        earliest_revoke_ms = fr_at
            OWNER_REVOKE_DEADLINES[tag] = earliest_revoke_ms

            # همگام‌سازی مأموریت‌های روزانه (در حالت فعال هر ۵ دقیقه، در حالت بیکاری هر ۲۵ دقیقه)
            mission_sync_interval = 1500 if is_deep_idle else 300
            if now_ts - last_mission_sync > mission_sync_interval:
                m_data = await safe_post(session, "missions", proxy=proxy)
                if m_data and isinstance(m_data.get("daily"), list):
                    for d in m_data["daily"]:
                        if d.get("id") == "d_rented10":
                            v = d.get("value", 0)
                            g = d.get("goal", 10)
                            claimed = d.get("claimed", False)
                            done = d.get("done", False) or (v >= g)
                            if not claimed and not done:
                                current_rent_needed = max(0, g - v)
                            else:
                                current_rent_needed = 0
                last_mission_sync = now_ts

            all_active_driver_contracts = [
                c for c in state_data.get("contracts", [])
                if c.get("status") == "active" and str(c.get("ownerId")) != my_id
            ]
            GLOBAL_FLEET_STATUS[tag] = {
                "AM": sum(1 for c in all_active_driver_contracts if c.get("shift") == "AM"),
                "PM": sum(1 for c in all_active_driver_contracts if c.get("shift") == "PM"),
                "rent_needed": current_rent_needed
            }

            utc_now = datetime.now(timezone.utc)
            current_shift = state_data.get("shift") or ("AM" if utc_now.hour < 12 else "PM")

            owner_rented_shifts = {
                (str(c.get("carId")), c.get("shift"))
                for c in state_data.get("contracts", [])
                if c.get("status") == "active" and str(c.get("ownerId")) == my_id
            }

            rented_cars = state_data.get("rentedCars", [])
            car_info_map = {}
            for c in rented_cars:
                c_id_str = str(c.get("id"))
                active_trip = c.get("trip") or {}
                trip_ends = active_trip.get("endsAt", 0)
                q = c.get("quota", {})

                car_info_map[c_id_str] = {
                    "name": c.get("name", "Rented Taxi"),
                    "car_quota_left": q.get("left", 0),
                    "quota_used": q.get("used", None),
                    "trip": active_trip if trip_ends > now_ms else None
                }

            driver_contracts = [
                c for c in all_active_driver_contracts
                if c.get("shift") == current_shift
            ]

            fleet = []

            # ۱. تاکسی‌های شخصی
            for car in state_data.get("myCars", []):
                car_id_str = str(car.get("id"))
                if (car_id_str, current_shift) in owner_rented_shifts:
                    continue

                own_quota = car.get("quota", {}).get("left", 0)
                active_trip = car.get("trip") or {}
                trip_ends = active_trip.get("endsAt", 0)
                car_name = car.get("name", "My Taxi")
                base_val = get_car_base_profit(car_name, "")

                fleet.append({
                    "id": car_id_str,
                    "name": car_name,
                    "type": "OWNED",
                    "trip_profit": base_val,
                    "trips_left": own_quota,
                    "today_used": car.get("quota", {}).get("used", 0),
                    "ends_at": 0,
                    "trip": active_trip if trip_ends > now_ms else None
                })

            # ۲. تاکسی‌های رنتی شیفت جاری
            for rc in driver_contracts:
                rc_car_id = str(rc.get("carId"))
                c_info = car_info_map.get(rc_car_id, {})
                car_raw_left = c_info.get("car_quota_left", 0)

                raw_d = rc.get("dailyTrips") or rc.get("tripsPerDay") or rc.get("trips") or 0
                if isinstance(raw_d, dict):
                    raw_d = raw_d.get("left", 0)
                rc_daily = int(raw_d) if str(raw_d).isdigit() else 0

                rc_used = rc.get("todayTrips")
                if rc_used is None:
                    rc_used = rc.get("tripsToday")
                if rc_used is None:
                    rc_used = rc.get("today")
                if rc_used is None:
                    rc_used = c_info.get("quota_used", 0)
                rc_used = int(rc_used) if str(rc_used).isdigit() else 0

                rc_quota = max(0, rc_daily - rc_used)
                if car_raw_left is not None:
                    rc_quota = min(rc_quota, int(car_raw_left))

                car_name = rc.get("name", "Rented Taxi")
                owner_pct = rc.get("ownerPct", 50)
                renter_pct = rc.get("myPct") or rc.get("renterPct") or (100 - owner_pct)
                base_val = get_car_base_profit(car_name, str(rc.get("modelId", "")))
                net_profit = base_val * (renter_pct / 100.0)
                ends_at = rc.get("contractEndsAt") or rc.get("endsAt") or 0

                fleet.append({
                    "id": rc_car_id,
                    "name": car_name,
                    "type": f"RENTED ({current_shift})",
                    "trip_profit": net_profit,
                    "trips_left": rc_quota,
                    "today_used": rc_used,
                    "ends_at": ends_at,
                    "trip": c_info.get("trip")
                })

            active_driving_car = next((car for car in fleet if car.get("trip")), None)

            if active_driving_car:
                is_deep_idle = False
                c_id = active_driving_car["id"]
                c_tag = f"#{c_id[-4:]}" if len(c_id) >= 4 else f"#{c_id}"
                c_type = active_driving_car["type"]
                active_trip = active_driving_car["trip"]
                ends_at = active_trip.get("endsAt", 0)
                wait_s = max(int((ends_at - now_ms) / 1000), 1)

                rem_m = wait_s // 60
                rem_s = wait_s % 60
                now_str = datetime.now().strftime("%H:%M:%S")
                log_event(f"[{now_str}] [{tag}] ⏳ Active trip in progress ({c_type} {c_tag}) | ~{rem_m}m {rem_s:02d}s remaining...")

                await asyncio.sleep(wait_s + random.randint(2, 4))

                post_state = None
                for _ in range(2):
                    post_state = await safe_post(session, "state", proxy=proxy)
                    if post_state and post_state.get("me"):
                        break
                    await asyncio.sleep(3)

                now_fin = datetime.now().strftime("%H:%M:%S")

                rem_left = 0
                if post_state:
                    if c_type == "OWNED":
                        for my_c in post_state.get("myCars", []):
                            if str(my_c.get("id")) == str(c_id):
                                rem_left = my_c.get("quota", {}).get("left", 0)
                                break
                    else:
                        for c_post in post_state.get("contracts", []):
                            if str(c_post.get("carId")) == str(c_id) and c_post.get("shift") == current_shift and str(c_post.get("ownerId")) != my_id:
                                p_daily = c_post.get("dailyTrips") or c_post.get("tripsPerDay") or c_post.get("trips") or 0
                                if isinstance(p_daily, dict):
                                    p_daily = p_daily.get("left", 0)
                                p_d = int(p_daily) if str(p_daily).isdigit() else 0
                                p_u = c_post.get("todayTrips") or c_post.get("tripsToday") or c_post.get("today") or 0
                                rem_left = max(0, p_d - int(p_u))
                                break

                earned_val = active_trip.get("earning") or active_trip.get("reward") or 0.015
                if post_state and post_state.get("me"):
                    new_usdt = float(post_state.get("balances", {}).get("usdt", 0.0))
                    new_cash = float(post_state.get("balances", {}).get("efectivo", 0.0))
                    log_event(f"[{now_fin}] [{tag}] ✅ Trip finished [{c_tag}] (+{earned_val} USDT | Bal: {new_usdt:.4f} USDT | Cash: {new_cash:.2f}) | {rem_left} trips left")
                else:
                    log_event(f"[{now_fin}] [{tag}] ✅ Trip finished [{c_tag}] (+{earned_val} USDT) | {rem_left} trips left")

                last_driven_id = c_id
                await asyncio.sleep(random.randint(20, 50))
                continue

            ready_cars = [c for c in fleet if c["trips_left"] > 0]

            if ready_cars:
                is_deep_idle = False
                ready_rented = [c for c in ready_cars if c["type"].startswith("RENTED")]

                def sort_priority(x):
                    is_rented = x["type"].startswith("RENTED")
                    if is_rented:
                        repeat_penalty = 1 if (x["id"] == last_driven_id and len(ready_rented) > 1) else 0
                        return (0, repeat_penalty, -x["trip_profit"])
                    else:
                        return (1, 0, -x["trip_profit"])

                ready_cars.sort(key=sort_priority)

                chosen_car = ready_cars[0]
                c_id = chosen_car["id"]
                c_tag = f"#{c_id[-4:]}" if len(c_id) >= 4 else f"#{c_id}"
                c_name = chosen_car["name"]
                c_type = chosen_car["type"]
                trips_left = chosen_car["trips_left"]

                trip_res = await safe_post(session, "trips/start", {"carId": c_id}, proxy=proxy)
                now_str = datetime.now().strftime("%H:%M:%S")

                if trip_res and (trip_res.get("trip") or trip_res.get("success")):
                    t_data = trip_res.get("trip", {})
                    earned_val = t_data.get("earning") or t_data.get("reward") or 0.0

                    ends_at = t_data.get("endsAt", 0)
                    trip_duration_sec = max(int((ends_at - now_ms) / 1000), 10) if ends_at > now_ms else 720
                    dur_m = trip_duration_sec // 60
                    dur_s = trip_duration_sec % 60

                    rem_left_start = max(0, trips_left - 1)
                    log_event(f"[{now_str}] [{tag}] 🚖 Trip started: {c_type} '{c_name}' [{c_tag}] ({rem_left_start} left) | Est: ~{dur_m}m {dur_s:02d}s")

                    await asyncio.sleep(trip_duration_sec + random.randint(2, 4))

                    post_state = None
                    for _ in range(2):
                        post_state = await safe_post(session, "state", proxy=proxy)
                        if post_state and post_state.get("me"):
                            break
                        await asyncio.sleep(3)

                    now_fin = datetime.now().strftime("%H:%M:%S")

                    rem_left_end = 0
                    if post_state:
                        if c_type == "OWNED":
                            for my_c in post_state.get("myCars", []):
                                if str(my_c.get("id")) == str(c_id):
                                    rem_left_end = my_c.get("quota", {}).get("left", 0)
                                    break
                        else:
                            for c_post in post_state.get("contracts", []):
                                if str(c_post.get("carId")) == str(c_id) and c_post.get("shift") == current_shift and str(c_post.get("ownerId")) != my_id:
                                    p_daily = c_post.get("dailyTrips") or c_post.get("tripsPerDay") or c_post.get("trips") or 0
                                    if isinstance(p_daily, dict):
                                        p_daily = p_daily.get("left", 0)
                                    p_d = int(p_daily) if str(p_daily).isdigit() else 0
                                    p_u = c_post.get("todayTrips") or c_post.get("tripsToday") or c_post.get("today") or 0
                                    rem_left_end = max(0, p_d - int(p_u))
                                    break

                    if post_state and post_state.get("me"):
                        new_usdt = float(post_state.get("balances", {}).get("usdt", 0.0))
                        new_cash = float(post_state.get("balances", {}).get("efectivo", 0.0))
                        log_event(f"[{now_fin}] [{tag}] ✅ Trip finished [{c_tag}] (+{earned_val} USDT | Bal: {new_usdt:.4f} USDT | Cash: {new_cash:.2f}) | {rem_left_end} trips left")
                    else:
                        log_event(f"[{now_fin}] [{tag}] ✅ Trip finished [{c_tag}] (+{earned_val} USDT) | {rem_left_end} trips left")

                    last_driven_id = c_id
                    await asyncio.sleep(random.randint(20, 50))
                    continue
                else:
                    err = trip_res.get("error") or trip_res.get("message") or "Unavailable"
                    log_event(f"[{now_str}] [{tag}] [Notice] {c_type} [{c_tag}] -> {err}")

            # --- محاسبه هوشمند زمان خواب (۳-۵ دقیقه در حالت فعال / ۲۰-۳۰ دقیقه در حالت اتمام سفرها) ---
            total_trips_left = sum(c["trips_left"] for c in fleet)
            now_str = datetime.now().strftime("%H:%M:%S")

            if total_trips_left > 0:
                # هنوز سفر باقی مانده (مثلاً به دلیل کول‌داون موقت شروع نشد) -> تایمر کوتاه ۳ تا ۵ دقیقه
                is_deep_idle = False
                sleep_time = random.randint(180, 300)
            else:
                # تمام سفرهای شیفت جاری به پایان رسیده -> خواب عمیق ۲۰ تا ۳۰ دقیقه
                sleep_time = random.randint(1200, 1800)

                # ایجاد فاصله پلکانی بین بیدارباش اکانت‌ها (~۱۵ دقیقه توزیع در کل ناوگان)
                if not has_staggered_idle:
                    sleep_time += (account_index % 10) * random.uniform(75.0, 105.0)
                    has_staggered_idle = True

                # جلوگیری از خوابیدن بیش از حد در نزدیکی تغییر شیفت (12:00 یا 00:00 UTC)
                utc_curr = datetime.now(timezone.utc)
                next_boundary_hour = 12 if utc_curr.hour < 12 else 24
                sec_to_shift_change = ((next_boundary_hour - utc_curr.hour - 1) * 3600) + ((60 - utc_curr.minute - 1) * 60) + (60 - utc_curr.second)
                if 0 < sec_to_shift_change < sleep_time:
                    sleep_time = sec_to_shift_change + random.uniform(20.0, 240.0)

                if not is_deep_idle:
                    m_slp = int(sleep_time // 60)
                    s_slp = int(sleep_time % 60)
                    log_event(f"[{now_str}] [{tag}] 💤 All trips completed. Entering smart idle (~{m_slp}m {s_slp:02d}s)...")
                is_deep_idle = True

            if user_wake_event:
                try:
                    await asyncio.wait_for(user_wake_event.wait(), timeout=sleep_time)
                    # بیدار شدن فوری بر اثر فسخ خودرو یا شکار تاکسی جدید
                    is_deep_idle = False
                    await asyncio.sleep(random.uniform(1.0, 3.5))
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(sleep_time)

async def fetch_account_full_data(acc):
    proxy = acc.get("proxy") or None
    tag = acc.get("name", "Account")
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(headers=get_headers(acc), connector=connector) as session:
        st, m_data = await asyncio.gather(
            safe_post(session, "state", proxy=proxy),
            safe_post(session, "missions", proxy=proxy)
        )

        if not st or not st.get("me"):
            return {"error": True, "acc": tag}

        my_id = str(st.get("me", {}).get("id", ""))
        username = st.get("me", {}).get("username", tag)[:12]
        usdt = float(st.get("balances", {}).get("usdt", 0.0))
        cash = float(st.get("balances", {}).get("efectivo", 0.0))

        current_shift = st.get("shift") or ("AM" if datetime.now(timezone.utc).hour < 12 else "PM")
        my_cars = st.get("myCars", [])
        cars_count = len(my_cars)

        owner_rented_shifts = {
            (str(c.get("carId")), c.get("shift"))
            for c in st.get("contracts", [])
            if c.get("status") == "active" and str(c.get("ownerId")) == my_id
        }

        own_left = 0
        rented_out_cars_count = 0

        for c in my_cars:
            c_id = str(c.get("id"))
            if (c_id, current_shift) in owner_rented_shifts:
                rented_out_cars_count += 1
            else:
                own_left += c.get("quota", {}).get("left", 0)

        if cars_count == 0:
            own_taxi_str = "------"
        elif own_left > 0:
            own_taxi_str = f"{own_left} left"
        elif rented_out_cars_count == cars_count:
            own_taxi_str = "0 left (Rented)"
        else:
            own_taxi_str = "0 left"

        rented_cars = st.get("rentedCars", [])
        car_raw_left_map = {str(c.get("id")): c.get("quota", {}).get("left") for c in rented_cars}

        def get_contract_trips_left(contract):
            raw_d = contract.get("dailyTrips") or contract.get("tripsPerDay") or contract.get("trips") or 0
            if isinstance(raw_d, dict):
                raw_d = raw_d.get("left", 0)
            d_trips = int(raw_d) if str(raw_d).isdigit() else 0

            used = contract.get("todayTrips")
            if used is None:
                used = contract.get("tripsToday")
            if used is None:
                used = contract.get("today")
            u_trips = int(used) if str(used).isdigit() else 0

            rem = max(0, d_trips - u_trips)
            c_car_id = str(contract.get("carId"))
            car_l = car_raw_left_map.get(c_car_id)
            if car_l is not None and str(car_l).isdigit():
                rem = min(rem, int(car_l))
            return rem

        driver_contracts = [
            c for c in st.get("contracts", [])
            if c.get("status") == "active" and str(c.get("ownerId")) != my_id
        ]
        in_am = [c for c in driver_contracts if c.get("shift") == "AM"]
        in_pm = [c for c in driver_contracts if c.get("shift") == "PM"]

        in_am_left = sum(get_contract_trips_left(c) for c in in_am) if current_shift == "AM" else 0
        in_pm_left = sum(get_contract_trips_left(c) for c in in_pm) if current_shift == "PM" else 0

        rented_am_str = f"{len(in_am)}/2 ({in_am_left:>2} left )" if in_am else "0/2 ( ------ )"
        rented_pm_str = f"{len(in_pm)}/2 ({in_pm_left:>2} left )" if in_pm else "0/2 ( ------ )"

        daily_10_str = "0/10"
        daily_rent_str = "0/10"
        daily_10_ready = False
        daily_rent_ready = False
        daily_10_claimed_flag = False
        daily_rent_claimed_flag = False
        rent_deficit = 0

        if m_data and isinstance(m_data.get("daily"), list):
            for d in m_data["daily"]:
                d_id = d.get("id")
                v = d.get("value", 0)
                g = d.get("goal", 10)
                done = d.get("done", False) or (v >= g)
                claimed = d.get("claimed", False)

                if d_id == "d_trips10":
                    if claimed:
                        daily_10_str = "Claimed"
                        daily_10_claimed_flag = True
                    elif done:
                        daily_10_str = "READY!"
                        daily_10_ready = True
                    else:
                        daily_10_str = f"{v}/{g}"

                elif d_id == "d_rented10":
                    if claimed:
                        daily_rent_str = "Claimed"
                        daily_rent_claimed_flag = True
                    elif done:
                        daily_rent_str = "READY!"
                        daily_rent_ready = True
                    else:
                        daily_rent_str = f"{v}/{g}"
                        rent_deficit = max(0, g - v)

        GLOBAL_FLEET_STATUS[tag] = {
            "AM": len(in_am),
            "PM": len(in_pm),
            "rent_needed": rent_deficit
        }

        all_trips_str = "0/300"
        rented_trips_str = "0/300"
        buy_taxi_str = "0/1"
        guild_str = "0/1"
        claimable_milestones = []

        all_trips_num = 0
        rented_trips_num = 0
        buy_taxi_num = 0
        guild_num = 0

        if m_data and isinstance(m_data.get("achievements"), list):
            ach_dict = {a.get("id"): a for a in m_data["achievements"]}

            all_trips_num = max([ach_dict.get(k, {}).get("value", 0) for k in ["a_trips300", "a_trips800", "a_trips1500"]] or [0])
            for t_id, goal in [("a_trips300", 300), ("a_trips800", 800), ("a_trips1500", 1500)]:
                a = ach_dict.get(t_id, {})
                v = a.get("value", 0)
                done = a.get("done", False) or (v >= goal)
                claimed = a.get("claimed", False)
                if not claimed:
                    if done:
                        all_trips_str = "READY!"
                        claimable_milestones.append(t_id)
                    else:
                        all_trips_str = f"{v}/{goal}"
                    break
            else:
                all_trips_str = "Done (1.5k)"

            rented_trips_num = max([ach_dict.get(k, {}).get("value", 0) for k in ["a_rented300", "a_rented500", "a_rented1500"]] or [0])
            for r_id, goal in [("a_rented300", 300), ("a_rented500", 500), ("a_rented1500", 1500)]:
                a = ach_dict.get(r_id, {})
                v = a.get("value", 0)
                done = a.get("done", False) or (v >= goal)
                claimed = a.get("claimed", False)
                if not claimed:
                    if done:
                        rented_trips_str = "READY!"
                        claimable_milestones.append(r_id)
                    else:
                        rented_trips_str = f"{v}/{goal}"
                    break
            else:
                rented_trips_str = "Done (1.5k)"

            buy_taxi_num = max([ach_dict.get(k, {}).get("value", 0) for k in ["a_buy1", "a_buy5", "a_buy10", "a_buy25"]] or [0])
            for b_id, goal in [("a_buy1", 1), ("a_buy5", 5), ("a_buy10", 10), ("a_buy25", 25)]:
                a = ach_dict.get(b_id, {})
                v = a.get("value", 0)
                done = a.get("done", False) or (v >= goal)
                claimed = a.get("claimed", False)
                if not claimed:
                    if done:
                        buy_taxi_str = "READY!"
                        claimable_milestones.append(b_id)
                    else:
                        buy_taxi_str = f"{v}/{goal}"
                    break
            else:
                buy_taxi_str = "Done (25)"

            g_ach = ach_dict.get("a_guild", {})
            if g_ach.get("claimed"):
                guild_str = "Claimed"
                guild_num = 1
            elif g_ach.get("done") or g_ach.get("value", 0) >= 1:
                guild_str = "READY!"
                guild_num = 1
                claimable_milestones.append("a_guild")
            else:
                guild_str = "0/1"
                guild_num = 0

        return {
            "error": False,
            "acc": tag,
            "user": username,
            "usdt": usdt,
            "cash": cash,
            "cars_count": cars_count,
            "own_taxi_str": own_taxi_str,
            "rented_am_str": rented_am_str,
            "rented_pm_str": rented_pm_str,
            "daily_10_str": daily_10_str,
            "daily_rent_str": daily_rent_str,
            "daily_10_ready": daily_10_ready,
            "daily_rent_ready": daily_rent_ready,
            "daily_10_claimed": daily_10_claimed_flag,
            "daily_rent_claimed": daily_rent_claimed_flag,
            "all_trips_str": all_trips_str,
            "rented_trips_str": rented_trips_str,
            "buy_taxi_str": buy_taxi_str,
            "guild_str": guild_str,
            "claimable_milestones": claimable_milestones,
            "own_left_num": own_left,
            "in_am_count": len(in_am),
            "in_am_left": in_am_left,
            "in_pm_count": len(in_pm),
            "in_pm_left": in_pm_left,
            "all_trips_num": all_trips_num,
            "rented_trips_num": rented_trips_num,
            "buy_taxi_num": buy_taxi_num,
            "guild_num": guild_num,
            "acc_data": acc
        }

def render_startup_dashboard(results):
    W = 114
    valid_r = [r for r in results if not r.get("error")]

    print("\n" + "=" * W, flush=True)
    print(">>> THE CRYPTO TAXI - CLOUD RUNNER DASHBOARD", flush=True)
    print("=" * W, flush=True)
    print(f"{'Account':^10} | {'User':^12} | {'USDT':^8} | {'Cash':^6} | {'Cars':^6} | {'Own Taxi':^17} | {'Rented AM':^17} | {'Rented PM':^17}", flush=True)
    print("-" * W, flush=True)

    for r in results:
        if r.get("error"):
            print(f"{r.get('acc', 'Acc'):<10} | {'-- Connection Error --':^101}", flush=True)
            continue
        usdt_str = f"{r['usdt']:.4f}"
        cash_str = f"{r['cash']:.2f}"
        print(f"{r['acc']:<10} | {r['user']:<12} | {usdt_str:^8} | {cash_str:^6} | {r['cars_count']:^6} | {r['own_taxi_str']:^17} | {r['rented_am_str']:^17} | {r['rented_pm_str']:^17}", flush=True)

    if valid_r:
        total_usdt = sum(r["usdt"] for r in valid_r)
        total_cash = sum(r["cash"] for r in valid_r)
        total_cars = sum(r["cars_count"] for r in valid_r)
        total_own_left = sum(r.get("own_left_num", 0) for r in valid_r)
        total_am_count = sum(r.get("in_am_count", 0) for r in valid_r)
        total_am_left = sum(r.get("in_am_left", 0) for r in valid_r)
        total_pm_count = sum(r.get("in_pm_count", 0) for r in valid_r)
        total_pm_left = sum(r.get("in_pm_left", 0) for r in valid_r)

        own_sum_str = f"{total_own_left} left"
        am_sum_str = f"{total_am_count}/{len(valid_r)*2} ({total_am_left:>2} left )"
        pm_sum_str = f"{total_pm_count}/{len(valid_r)*2} ({total_pm_left:>2} left )"

        print("-" * W, flush=True)
        print(f"{'SUM':<10} | {f'{len(valid_r)} Accs':<12} | {f'{total_usdt:.4f}':^8} | {f'{total_cash:.2f}':^6} | {total_cars:^6} | {own_sum_str:^17} | {am_sum_str:^17} | {pm_sum_str:^17}", flush=True)
    print("=" * W + "\n", flush=True)

async def cloud_main():
    accounts = load_accounts()
    valid_accounts = [a for a in accounts if a.get("cookie") and a.get("device_id")]
    if not valid_accounts:
        print("[-] No valid accounts loaded. Terminating run.", flush=True)
        sys.exit(1)

    print(f"[*] Cloud Runner initialized for {len(valid_accounts)} accounts.", flush=True)
    print(f"[*] Maximum session duration: 5 hours and 50 minutes ({MAX_RUNTIME_SECONDS} seconds).\n", flush=True)

    print("[*] Fetching initial fleet state...", flush=True)
    results = await asyncio.gather(*(fetch_account_full_data(a) for a in valid_accounts))
    render_startup_dashboard(results)

    # راه‌اندازی رویدادهای بیدارباش
    for acc in valid_accounts:
        ACCOUNT_WAKE_EVENTS[acc.get("name")] = asyncio.Event()

    # تسک‌های پس‌زمینه: اسنایپر و دیده‌بان فسخ رایگان خودکار
    BACKGROUND_TASKS.append(asyncio.create_task(background_market_sniper(valid_accounts)))
    BACKGROUND_TASKS.append(asyncio.create_task(background_auto_revoke_watcher(valid_accounts)))

    # دسته‌بندی اکانت‌ها بر اساس داشتن سفر آماده
    utc_now = datetime.now(timezone.utc)
    current_shift = "AM" if utc_now.hour < 12 else "PM"

    res_map = {r.get("acc"): r for r in results if not r.get("error")}
    active_accounts = []
    idle_accounts = []

    for acc in valid_accounts:
        tag = acc.get("name")
        r = res_map.get(tag)
        has_trips = False
        if r:
            own_left = r.get("own_left_num", 0)
            rent_left = r.get("in_am_left", 0) if current_shift == "AM" else r.get("in_pm_left", 0)
            if own_left > 0 or rent_left > 0:
                has_trips = True
        else:
            has_trips = True

        if has_trips:
            active_accounts.append(acc)
        else:
            idle_accounts.append(acc)

    random.shuffle(active_accounts)
    current_stagger = 0.0
    for idx, acc in enumerate(active_accounts):
        BACKGROUND_TASKS.append(asyncio.create_task(run_taxi_for_account(acc, current_stagger, account_index=idx)))
        current_stagger += random.uniform(20.0, 35.0)

    # پخش کردن زمان بیدارباش اکانت‌های بیکار در بازه ~۱۵ تا ۲۰ دقیقه
    random.shuffle(idle_accounts)
    idle_stagger = random.uniform(2.0, 12.0)
    for idx, acc in enumerate(idle_accounts):
        BACKGROUND_TASKS.append(asyncio.create_task(run_taxi_for_account(acc, idle_stagger, account_index=idx)))
        idle_stagger += random.uniform(80.0, 130.0)

    if active_accounts:
        act_names = ", ".join(a.get("name") for a in active_accounts)
        print(f"[*] Dispatched {len(active_accounts)} active account(s) (First starts immediately): [{act_names}]", flush=True)

    if idle_accounts:
        idle_names = ", ".join(a.get("name") for a in idle_accounts)
        print(f"[*] Staggered {len(idle_accounts)} idle account(s) across ~{int(idle_stagger // 60)}m window: [{idle_names}]\n", flush=True)

    print(">>> ALL WORKERS & BACKGROUND WATCHERS DISPATCHED. RUNNING IN CLOUD UNTIL TIMEOUT...", flush=True)

    try:
        await asyncio.sleep(MAX_RUNTIME_SECONDS)
        print(f"\n[⏱️] Reached maximum cloud runtime of 5h 50m ({MAX_RUNTIME_SECONDS}s).", flush=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        print("\n[!] Cloud run interrupted.", flush=True)
    finally:
        print("[*] Gracefully cancelling background tasks...", flush=True)
        for t in BACKGROUND_TASKS:
            t.cancel()
        await asyncio.gather(*BACKGROUND_TASKS, return_exceptions=True)
        BACKGROUND_TASKS.clear()
        print("[✔] Cloud session finished cleanly. Exiting with code 0.", flush=True)
        sys.exit(0)

def main():
    try:
        asyncio.run(cloud_main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)

if __name__ == "__main__":
    main()
