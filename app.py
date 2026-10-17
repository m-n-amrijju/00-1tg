from flask import Flask, request, jsonify
from telethon import TelegramClient, types
from telethon.sessions import StringSession
from telethon.errors import (
    FloodWaitError, 
    UsernameInvalidError, 
    UsernameNotOccupiedError,
    SessionRevokedError,
    AuthKeyUnregisteredError,
    UserDeactivatedBanError,
    AuthKeyDuplicatedError
)
from telethon.tl.functions.users import GetFullUserRequest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import ChannelParticipantsAdmins
import asyncio
import os
import time
import random
import requests
from datetime import datetime, timedelta

app = Flask(__name__)

# ============================================
# CONFIG
# ============================================
API_ID = int(os.environ.get('API_ID', '36879151'))
API_HASH = os.environ.get('API_HASH', '45360a236343352099ffa29570f48700')
SESSION_STRING = os.environ.get('SESSION_STRING', '1BVtsOI4Bu2aqYwbxzr4l6E-jImpT8xpCD9XryD71Qq8bcPVb1H1ebSanXLS_PWl1OaJXsofxC5NgstzMBVhuyn66KA6e18xf_PinLp24alIuAFu-wWHkwsdyWyH6lS1UjSmBIdOlosAsksijT32BXL912p5_FOnssgSjTGamz1oJZtrye-1vzqE9v3DPh-Rrz4uva5eF_lR7vaZxiJCGuLjDz0eBrGMVZuJaoTpm1HXcqUgs4QQPUthAkiA6etphcONP4IwiVyEtzBcYzb8MSPN7TvlQazOihV5rx_hN9uMmST58fNTpAY5mDnEZ6GBkZGPTld-fXxCjP2YjMzsfL7BB15vqoLE=')

# ============================================
# PROXY SYSTEM
# ============================================
proxy_pool = []
proxy_last_fetch = 0

def fetch_proxies():
    global proxy_pool, proxy_last_fetch
    if time.time() - proxy_last_fetch < 300 and proxy_pool:
        return proxy_pool
    
    proxies = []
    sources = [
        "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000",
        "https://www.proxy-list.download/api/v1/get?type=http",
    ]
    for url in sources:
        try:
            r = requests.get(url, timeout=5)
            if r.status_code == 200:
                for line in r.text.strip().split('\n'):
                    line = line.strip()
                    if line and ':' in line and len(line) < 25:
                        proxies.append(line)
        except:
            pass
    
    proxies = list(set(proxies))
    if proxies:
        proxy_pool = proxies
        proxy_last_fetch = time.time()
    return proxy_pool


# ============================================
# CACHE
# ============================================
cache = {}
CACHE_TTL = 1800  # 30 minutes

def get_cached(key):
    if key in cache:
        r, t = cache[key]
        if datetime.now() - t < timedelta(seconds=CACHE_TTL):
            return r
    return None

def set_cached(key, val):
    cache[key] = (val, datetime.now())


# ============================================
# GLOBAL CLIENT STATE
# ============================================
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
client = None
client_status = {"authorized": False, "error": None, "user": None}


def create_client():
    """Create Telegram client safely"""
    global client
    if not SESSION_STRING:
        client_status["error"] = "SESSION_STRING env variable is empty!"
        return None
    
    try:
        client = TelegramClient(
            StringSession(SESSION_STRING),
            API_ID,
            API_HASH,
            loop=loop,
            connection_retries=5,
            retry_delay=2,
            timeout=30,
            auto_reconnect=True
        )
        return client
    except Exception as e:
        client_status["error"] = f"Client creation failed: {str(e)}"
        return None


async def init_client():
    """Initialize client and check authorization"""
    global client, client_status
    
    if client is None:
        client_status["error"] = "Client not initialized"
        return False
    
    try:
        await client.connect()
        
        if not await client.is_user_authorized():
            client_status["authorized"] = False
            client_status["error"] = "SESSION_STRING invalid/expired. Generate new one!"
            return False
        
        me = await client.get_me()
        client_status["authorized"] = True
        client_status["error"] = None
        client_status["user"] = {
            "id": me.id,
            "first_name": me.first_name,
            "username": me.username,
            "phone": me.phone
        }
        return True
    except SessionRevokedError:
        client_status["authorized"] = False
        client_status["error"] = "Session revoked! Generate new SESSION_STRING."
        return False
    except AuthKeyUnregisteredError:
        client_status["authorized"] = False
        client_status["error"] = "Auth key unregistered! Generate new SESSION_STRING."
        return False
    except AuthKeyDuplicatedError:
        client_status["authorized"] = False
        client_status["error"] = "Auth key duplicated! Generate new SESSION_STRING."
        return False
    except Exception as e:
        client_status["authorized"] = False
        client_status["error"] = f"Init failed: {str(e)}"
        return False


def ensure_connected():
    """Ensure client is connected"""
    global client
    if client is None:
        create_client()
    return client


# ============================================
# HELPERS
# ============================================
def sg(obj, attr, default=None):
    try:
        v = getattr(obj, attr, default)
        return v if v is not None else default
    except:
        return default


def fmt_date(dt):
    if dt is None:
        return None
    try:
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except:
        return str(dt)


def human_time(dt):
    if not dt:
        return None
    try:
        diff = datetime.now(dt.tzinfo) - dt
        secs = int(diff.total_seconds())
        if secs < 60: return f"{secs}s ago"
        if secs < 3600: return f"{secs//60}m ago"
        if secs < 86400: return f"{secs//3600}h ago"
        if secs < 604800: return f"{secs//86400}d ago"
        if secs < 2592000: return f"{secs//604800}w ago"
        if secs < 31536000: return f"{secs//2592000}mo ago"
        return f"{secs//31536000}y ago"
    except:
        return None


async def get_entity(identifier, retry=0):
    """Get entity with full error handling"""
    global client
    
    if client is None:
        raise Exception("Client not initialized")
    
    try:
        await client.connect()
    except Exception as e:
        raise Exception(f"Connection failed: {str(e)}")
    
    try:
        if not await client.is_user_authorized():
            raise Exception("SESSION_STRING invalid/expired. Generate new session!")
    except Exception as e:
        raise Exception(f"Auth failed: {str(e)}")
    
    try:
        if identifier.startswith('@'):
            return await client.get_entity(identifier)
        elif identifier.lstrip('-').isdigit():
            return await client.get_entity(int(identifier))
        else:
            return await client.get_entity(f"@{identifier}")
    
    except FloodWaitError as e:
        if retry < 2 and e.seconds <= 30:
            await asyncio.sleep(e.seconds)
            return await get_entity(identifier, retry + 1)
        raise Exception(f"Flood wait: {e.seconds}s")
    
    except (UsernameInvalidError, UsernameNotOccupiedError):
        raise Exception(f"Username '{identifier}' not found")
    
    except ValueError as e:
        raise Exception(f"Invalid identifier: {identifier}")
    
    except SessionRevokedError:
        raise Exception("SESSION_STRING revoked! Generate new one.")
    
    except Exception as e:
        err = str(e)
        if "Cannot find any entity" in err:
            raise Exception(f"Entity not found: {identifier}")
        if "not authorized" in err.lower():
            raise Exception("SESSION_STRING expired! Generate new one.")
        if "Could not find the input entity" in err:
            raise Exception(f"Cannot resolve: {identifier}")
        raise Exception(err)


# ============================================
# 🧠 INTELLIGENCE ANALYZERS
# ============================================

def intel_online_status(entity):
    status = sg(entity, 'status')
    if not status:
        return {"status": "⚪ Unknown", "last_seen": "No data", "privacy": "hidden"}
    
    t = type(status).__name__
    
    if t == "UserStatusOnline":
        return {"status": "🟢 Online Now", "last_seen": "Active right now", "privacy": "public"}
    
    if t == "UserStatusOffline":
        wo = sg(status, 'was_online')
        return {
            "status": "🔴 Offline",
            "last_seen": human_time(wo),
            "exact_time": fmt_date(wo),
            "privacy": "public"
        }
    
    if t == "UserStatusRecently":
        return {"status": "🟡 Recently Active", "last_seen": "Within 2 days", "privacy": "recently"}
    
    if t == "UserStatusLastWeek":
        return {"status": "🟠 Last Week", "last_seen": "Within 7 days", "privacy": "week"}
    
    if t == "UserStatusLastMonth":
        return {"status": "⚫ Last Month", "last_seen": "Within 30 days", "privacy": "month"}
    
    if t == "UserStatusEmpty":
        return {"status": "❓ Hidden", "last_seen": "Privacy restricted", "privacy": "hidden"}
    
    return {"status": "⚪ Unknown", "last_seen": str(status), "privacy": "unknown"}


def intel_account_type(entity, info):
    flags = []
    if sg(entity, 'bot'): flags.append("🤖 Bot")
    if sg(entity, 'premium'): flags.append("💎 Premium")
    if sg(entity, 'verified'): flags.append("✅ Verified")
    if sg(entity, 'scam'): flags.append("🚨 Scam")
    if sg(entity, 'fake'): flags.append("⚠️ Fake")
    if sg(entity, 'deleted'): flags.append("💀 Deleted")
    if sg(entity, 'restricted'): flags.append("🔒 Restricted")
    if sg(entity, 'support'): flags.append("🛡️ Support")
    if sg(entity, 'contact'): flags.append("📇 Contact")
    if sg(entity, 'mutual_contact'): flags.append("🤝 Mutual")
    if sg(entity, 'close_friend'): flags.append("💚 Close Friend")
    
    if sg(entity, 'bot'): primary = "Bot Account"
    elif sg(entity, 'deleted'): primary = "Deleted Account"
    elif sg(entity, 'scam'): primary = "Scam Account"
    elif sg(entity, 'fake'): primary = "Fake Account"
    elif sg(entity, 'verified'): primary = "Verified Account"
    elif sg(entity, 'premium'): primary = "Premium User"
    else: primary = "Regular User"
    
    return {"primary_type": primary, "badges": flags}


def intel_identity_strength(entity, info):
    score = 0
    checks = []
    
    fn = sg(entity, 'first_name', '') or ''
    ln = sg(entity, 'last_name', '') or ''
    if fn and ln:
        score += 20; checks.append("full_name")
    elif fn:
        score += 10; checks.append("partial_name")
    
    if sg(entity, 'username'):
        score += 20; checks.append("username")
    
    if info.get("photo", {}).get("count", 0) > 0:
        score += 15; checks.append("has_photo")
        if info["photo"]["count"] >= 3:
            score += 5; checks.append("multiple_photos")
    
    if info.get("bio"):
        score += 15; checks.append("has_bio")
        if len(info["bio"]) >= 30:
            score += 5; checks.append("detailed_bio")
    
    if sg(entity, 'verified'): score += 10; checks.append("verified")
    if sg(entity, 'premium'): score += 10; checks.append("premium")
    
    return {
        "score": min(100, score),
        "level": "Strong" if score >= 70 else "Medium" if score >= 40 else "Weak",
        "checks_passed": checks
    }


def intel_trust_score(entity, info):
    score = 50
    factors = []
    
    if sg(entity, 'verified'): score += 25; factors.append("+25 Verified")
    if sg(entity, 'premium'): score += 10; factors.append("+10 Premium")
    if info.get("identity_strength", {}).get("score", 0) >= 70:
        score += 15; factors.append("+15 Strong identity")
    if sg(entity, 'username'): score += 5; factors.append("+5 Has username")
    
    if sg(entity, 'scam'): score -= 40; factors.append("-40 Marked Scam")
    if sg(entity, 'fake'): score -= 30; factors.append("-30 Marked Fake")
    if sg(entity, 'deleted'): score -= 50; factors.append("-50 Deleted")
    if sg(entity, 'restricted'): score -= 20; factors.append("-20 Restricted")
    if not info.get("photo", {}).get("count"): 
        score -= 5; factors.append("-5 No photo")
    if not sg(entity, 'username'): 
        score -= 5; factors.append("-5 No username")
    
    score = max(0, min(100, score))
    
    if score >= 80: level, emoji = "Highly Trusted", "💎"
    elif score >= 60: level, emoji = "Trusted", "✅"
    elif score >= 40: level, emoji = "Neutral", "⚖️"
    elif score >= 20: level, emoji = "Suspicious", "⚠️"
    else: level, emoji = "Dangerous", "🚨"
    
    return {"score": f"{score}/100", "level": level, "emoji": emoji, "factors": factors}


def intel_activity_pattern(entity, info):
    activity = info.get("online_status", {})
    account_age = info.get("account_age_days")
    
    st = activity.get("status", "")
    if "🟢" in st: pattern, eng = "🔥 Highly Active", "Very High"
    elif "🟡" in st: pattern, eng = "✅ Active", "High"
    elif "🟠" in st: pattern, eng = "📅 Moderately Active", "Medium"
    elif "⚫" in st: pattern, eng = "💤 Low Activity", "Low"
    elif "❓" in st: pattern, eng = "🕵️ Privacy Hidden", "Unknown"
    else: pattern, eng = "❓ Unknown", "Unknown"
    
    return {
        "pattern": pattern,
        "engagement_level": eng,
        "account_age": f"{account_age} days" if account_age else None
    }


def intel_quality_report(entity, info):
    points = []
    score = 0
    
    fn = sg(entity, 'first_name', '') or ''
    ln = sg(entity, 'last_name', '') or ''
    
    if fn and len(fn) >= 2:
        score += 15; points.append("✓ Proper first name")
    if ln:
        score += 10; points.append("✓ Full name provided")
    
    uname = sg(entity, 'username')
    if uname:
        score += 15; points.append(f"✓ Username: @{uname}")
        if len(uname) >= 5 and not uname.isdigit():
            score += 5; points.append("✓ Quality username length")
    
    pc = info.get("photo", {}).get("count", 0)
    if pc > 0:
        score += 15; points.append(f"✓ Profile photo ({pc})")
    
    bio = info.get("bio", "")
    if len(bio) >= 30:
        score += 15; points.append(f"✓ Detailed bio ({len(bio)} chars)")
    elif bio:
        score += 8; points.append(f"✓ Has bio ({len(bio)} chars)")
    
    if sg(entity, 'verified'): score += 10; points.append("✓ Verified")
    if sg(entity, 'premium'): score += 7; points.append("✓ Premium")
    
    cc = info.get("common_chats", 0)
    if cc > 0:
        score += 5; points.append(f"✓ {cc} common chats")
    
    score = max(0, min(100, score))
    
    if score >= 80: rating, emoji = "Elite Profile", "💎"
    elif score >= 60: rating, emoji = "High Quality", "⭐"
    elif score >= 40: rating, emoji = "Decent", "✅"
    elif score >= 20: rating, emoji = "Basic", "⚠️"
    else: rating, emoji = "Poor/Suspicious", "🚨"
    
    return {"score": f"{score}/100", "rating": rating, "emoji": emoji, "points": points}


async def intel_photo_data(entity):
    result = {"count": 0, "latest": None, "oldest": None}
    try:
        photos = await client.get_profile_photos(entity, limit=20)
        result["count"] = len(photos)
        if photos:
            latest = photos[0]
            oldest = photos[-1]
            result["latest"] = {
                "id": latest.id,
                "date": fmt_date(sg(latest, 'date')),
                "dc_id": sg(latest, 'dc_id')
            }
            result["oldest"] = {
                "id": oldest.id,
                "date": fmt_date(sg(oldest, 'date'))
            }
    except:
        pass
    return result


def intel_restrictions(entity):
    restr = []
    
    if sg(entity, 'restricted'):
        reasons = sg(entity, 'restriction_reason', []) or []
        for r in reasons:
            restr.append({
                "type": sg(r, 'platform', 'restricted'),
                "reason": sg(r, 'reason', 'N/A'),
                "text": sg(r, 'text', 'N/A')
            })
    
    if sg(entity, 'scam'): restr.append({"type": "flag", "reason": "Marked as scam"})
    if sg(entity, 'fake'): restr.append({"type": "flag", "reason": "Marked as fake"})
    if sg(entity, 'deleted'): restr.append({"type": "status", "reason": "Account deleted"})
    
    if sg(entity, 'noforwards'): restr.append({"type": "setting", "reason": "Forwarding disabled"})
    if sg(entity, 'join_request'): restr.append({"type": "setting", "reason": "Join request required"})
    if sg(entity, 'slowmode_enabled'):
        restr.append({"type": "setting", "reason": f"Slowmode: {sg(entity, 'slowmode_seconds', 0)}s"})
    if sg(entity, 'participants_hidden'): restr.append({"type": "setting", "reason": "Members hidden"})
    if sg(entity, 'pre_history_hidden'): restr.append({"type": "setting", "reason": "History hidden"})
    
    return restr if restr else "None"


async def intel_admins(entity, limit=15):
    result = []
    try:
        if isinstance(entity, types.Channel):
            admins = await client.get_participants(
                entity, 
                filter=ChannelParticipantsAdmins, 
                limit=limit
            )
            for a in admins:
                try:
                    p = await client.get_permissions(entity, a)
                    role = "Owner" if getattr(p, 'is_creator', False) else "Admin"
                    rights = []
                    if getattr(p, 'delete_messages', False): rights.append("delete")
                    if getattr(p, 'ban_users', False): rights.append("ban")
                    if getattr(p, 'invite_users', False): rights.append("invite")
                    if getattr(p, 'pin_messages', False): rights.append("pin")
                    if getattr(p, 'add_admins', False): rights.append("add_admins")
                    
                    result.append({
                        "id": a.id,
                        "username": sg(a, 'username'),
                        "name": f"{sg(a, 'first_name', '')} {sg(a, 'last_name', '')}".strip(),
                        "role": role,
                        "is_bot": sg(a, 'bot', False),
                        "rights": rights
                    })
                except:
                    result.append({
                        "id": a.id,
                        "username": sg(a, 'username'),
                        "name": f"{sg(a, 'first_name', '')} {sg(a, 'last_name', '')}".strip()
                    })
    except:
        pass
    return result


# ============================================
# 🎯 MAIN INTEL EXTRACTOR
# ============================================
async def extract_intel(identifier):
    entity = await get_entity(identifier)
    
    is_user = isinstance(entity, types.User)
    is_channel = isinstance(entity, types.Channel)
    is_group = isinstance(entity, types.Chat)
    
    core = {
        "type": "user" if is_user else ("channel" if sg(entity, 'broadcast') else "supergroup") if is_channel else "group",
        "bio": "",
        "common_chats": 0,
        "account_age_days": None
    }
    
    # Full entity
    full_entity = None
    try:
        if is_user:
            full_entity = (await client(GetFullUserRequest(entity))).full_user
            core["bio"] = sg(full_entity, 'about', '')
            core["common_chats"] = sg(full_entity, 'common_chats_count', 0)
        elif is_channel:
            full_entity = (await client(GetFullChannelRequest(entity))).full_chat
            core["bio"] = sg(full_entity, 'about', '')
        elif is_group:
            full_entity = (await client(GetFullChatRequest(entity.id))).full_chat
    except:
        pass
    
    # Account age
    date_created = sg(entity, 'date')
    if date_created:
        try:
            core["account_age_days"] = (datetime.now(date_created.tzinfo) - date_created).days
        except:
            pass
    
    # Photos
    photo_data = await intel_photo_data(entity)
    
    # ============================================
    # BUILD INTEL
    # ============================================
    intel = {
        "status": "success",
        "credit": "@BRONX_ULTRA",
        "version": "4.1 Elite Intel",
        "query": identifier,
        "extracted_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        
        "identity": {
            "chat_id": entity.id,
            "access_hash": sg(entity, 'access_hash'),
            "type": core["type"],
            "username": sg(entity, 'username'),
            "all_usernames": [u.username for u in sg(entity, 'usernames', [])] if hasattr(entity, 'usernames') else [],
            "first_name": sg(entity, 'first_name'),
            "last_name": sg(entity, 'last_name'),
            "full_name": f"{sg(entity, 'first_name', '')} {sg(entity, 'last_name', '')}".strip() or None,
            "title": sg(entity, 'title'),
            "phone": sg(entity, 'phone'),
            "language": sg(entity, 'lang_code'),
        },
        
        "online_status": intel_online_status(entity),
        "photo": photo_data,
        "bio": core["bio"] or None,
        "bio_length": len(core["bio"]) if core["bio"] else 0,
        "account": intel_account_type(entity, {}),
        "channel_data": None,
        "restrictions": intel_restrictions(entity),
        
        "related": {
            "common_chats": core["common_chats"],
            "personal_channel": None,
            "linked_chat_id": sg(entity, 'linked_chat_id') or (sg(full_entity, 'linked_chat_id') if full_entity else None)
        },
        
        "identity_strength": None,
        "trust_score": None,
        "activity_pattern": None,
        "quality": None,
        
        "proxy_pool": len(proxy_pool),
        "cached": False,
    }
    
    # Personal channel
    if is_user and full_entity:
        pc = sg(full_entity, 'personal_chat')
        if pc:
            intel["related"]["personal_channel"] = {
                "id": pc.id,
                "username": sg(pc, 'username'),
                "title": sg(pc, 'title')
            }
    
    # Channel/Group specific
    if is_channel or is_group:
        ch_data = {
            "created": fmt_date(date_created),
            "verified": sg(entity, 'verified', False),
            "members": sg(full_entity, 'participants_count') if full_entity else sg(entity, 'participants_count'),
            "online": sg(full_entity, 'online_count') if full_entity else None,
            "admins": sg(full_entity, 'admins_count') if full_entity else None,
            "banned": sg(full_entity, 'banned_count') if full_entity else None,
            "is_megagroup": sg(entity, 'megagroup', False),
            "is_broadcast": sg(entity, 'broadcast', False),
            "is_gigagroup": sg(entity, 'gigagroup', False),
            "signatures_enabled": sg(entity, 'signatures', False),
            "antispam": sg(entity, 'antispam', False),
            "pinned_message_id": sg(full_entity, 'pinned_msg_id') if full_entity else None,
            "reactions_count": len(sg(full_entity, 'available_reactions', []) or []) if full_entity else 0,
        }
        intel["channel_data"] = ch_data
        
        admins = await intel_admins(entity, limit=15)
        if admins:
            intel["admins"] = admins
    
    # User settings
    if is_user and full_entity:
        intel["user_settings"] = {
            "blocked": sg(full_entity, 'blocked', False),
            "blocked_stories": sg(full_entity, 'blocked_my_stories', False),
            "phone_calls": sg(full_entity, 'phone_calls_available', False),
            "video_calls": sg(full_entity, 'video_calls_available', False),
            "voice_messages_forbidden": sg(full_entity, 'voice_messages_forbidden', False),
            "ttl_period": sg(full_entity, 'ttl_period', 0),
            "theme_emoji": sg(full_entity, 'theme_emoticon'),
            "private_forward_name": sg(full_entity, 'private_forward_name'),
        }
        
        bot = sg(full_entity, 'bot_info')
        if bot:
            intel["bot_info"] = {
                "description": sg(bot, 'description'),
                "commands_count": len(sg(bot, 'commands', []) or []),
                "commands": [
                    {"cmd": c.command, "desc": c.description}
                    for c in (sg(bot, 'commands', []) or [])[:10]
                ],
                "privacy_policy": sg(bot, 'privacy_policy_url')
            }
    
    # Run analyzers
    intel["identity_strength"] = intel_identity_strength(entity, intel)
    intel["trust_score"] = intel_trust_score(entity, intel)
    intel["activity_pattern"] = intel_activity_pattern(entity, intel)
    intel["quality"] = intel_quality_report(entity, intel)
    
    return intel


# ============================================
# ROUTES
# ============================================
@app.route('/')
def home():
    auth_status = "✅ Authorized" if client_status["authorized"] else "❌ Not Authorized"
    auth_color = "#0f0" if client_status["authorized"] else "#f00"
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head><title>BRONX ULTRA v4.1</title>
    <style>
        body {{ background:#000; color:#0ff; font-family:monospace; text-align:center; padding:40px; }}
        h1 {{ color:#f0f; text-shadow:0 0 20px #f0f; }}
        code {{ background:#111; padding:10px; color:#fa0; border-radius:5px; display:block; margin:10px auto; max-width:600px; }}
        .box {{ border:1px solid #0ff; padding:20px; margin:20px auto; max-width:700px; border-radius:10px; }}
        .ok {{ color: {auth_color}; }}
        .err {{ color: #f00; }}
    </style></head>
    <body>
        <h1>💀 BRONX ULTRA v4.1</h1>
        <h3>🎯 ELITE INTEL EDITION</h3>
        <div class="box">
            <p class="ok">🔐 Client Status: {auth_status}</p>
            <p>🛡️ Proxies: {len(proxy_pool)} | 💾 Cache: {len(cache)}</p>
        </div>
        <div class="box">
            <code>GET /intel?username=USERNAME</code>
            <code>GET /chatid?username=USERNAME</code>
            <code>GET /health</code>
        </div>
        <p style="color:#f0f;">@BRONX_ULTRA</p>
    </body></html>
    """


@app.route('/intel')
def intel_endpoint():
    """Elite intel endpoint"""
    identifier = (request.args.get('username') or request.args.get('id') or request.args.get('query', '')).strip()
    
    if not identifier:
        return jsonify({
            "status": "error",
            "message": "Missing 'username' or 'id' parameter",
            "credit": "@BRONX_ULTRA"
        }), 400
    
    # Auth check
    if not client_status["authorized"]:
        return jsonify({
            "status": "error",
            "message": client_status.get("error") or "Client not authorized",
            "fix": "Generate new SESSION_STRING and update env variable",
            "credit": "@BRONX_ULTRA"
        }), 401
    
    # Cache check
    key = f"intel:{identifier.lower()}"
    c = get_cached(key)
    if c:
        c["cached"] = True
        return jsonify(c)
    
    try:
        result = loop.run_until_complete(extract_intel(identifier))
        set_cached(key, result)
        return jsonify(result)
    
    except FloodWaitError as e:
        return jsonify({
            "status": "error",
            "message": f"Flood wait: {e.seconds}s",
            "credit": "@BRONX_ULTRA"
        }), 429
    
    except Exception as e:
        err = str(e)
        return jsonify({
            "status": "error",
            "message": err,
            "credit": "@BRONX_ULTRA"
        }), 404


@app.route('/chatid')
def chatid():
    u = request.args.get('username', '').strip()
    if not u:
        return jsonify({"status": "error", "message": "Missing username", "credit": "@BRONX_ULTRA"}), 400
    
    if not client_status["authorized"]:
        return jsonify({
            "status": "error",
            "message": client_status.get("error") or "Client not authorized",
            "credit": "@BRONX_ULTRA"
        }), 401
    
    try:
        e = loop.run_until_complete(get_entity(u))
        return jsonify({
            "status": "success",
            "chat_id": e.id,
            "username": sg(e, 'username', u.replace('@', '')),
            "type": "user" if isinstance(e, types.User) else ("channel" if sg(e, 'broadcast') else "group"),
            "credit": "@BRONX_ULTRA"
        })
    except Exception as ex:
        return jsonify({"status": "error", "message": str(ex), "credit": "@BRONX_ULTRA"}), 404


@app.route('/health')
def health():
    return jsonify({
        "status": "ok",
        "version": "4.1",
        "client_authorized": client_status["authorized"],
        "client_error": client_status.get("error"),
        "client_user": client_status.get("user"),
        "cached": len(cache),
        "proxies": len(proxy_pool),
        "credit": "@BRONX_ULTRA"
    })


@app.route('/clear-cache')
def clear_cache():
    cache.clear()
    return jsonify({"status": "success", "message": "Cleared", "credit": "@BRONX_ULTRA"})


@app.route('/reconnect')
def reconnect():
    """Force reconnect client"""
    ok = loop.run_until_complete(init_client())
    return jsonify({
        "status": "success" if ok else "error",
        "authorized": client_status["authorized"],
        "error": client_status.get("error"),
        "user": client_status.get("user"),
        "credit": "@BRONX_ULTRA"
    })


# ============================================
# MAIN
# ============================================
if __name__ == "__main__":
    print("=" * 60)
    print("💀 BRONX ULTRA v4.1 - STARTING")
    print("=" * 60)
    print(f"📋 API_ID: {API_ID}")
    print(f"📋 API_HASH: {API_HASH[:10]}...{API_HASH[-5:]}")
    
    if not SESSION_STRING:
        print("❌ SESSION_STRING is EMPTY! Set env variable.")
    else:
        print(f"📋 SESSION length: {len(SESSION_STRING)}")
    print()
    
    fetch_proxies()
    print(f"🛡️ Proxies loaded: {len(proxy_pool)}\n")
    
    # Create + init client
    create_client()
    ok = loop.run_until_complete(init_client())
    
    if ok:
        u = client_status["user"]
        print("=" * 60)
        print("✅ AUTHORIZED!")
        print(f"   👤 {u['first_name']}")
        print(f"   📛 @{u['username']}")
        print(f"   🆔 {u['id']}")
        print("=" * 60)
        print("💀 BRONX ULTRA v4.1 - READY")
        print("=" * 60)
    else:
        print("=" * 60)
        print("❌ NOT AUTHORIZED!")
        print(f"   Error: {client_status.get('error')}")
        print("=" * 60)
        print("🔧 FIX STEPS:")
        print("   1. Run generate_session.py locally")
        print("   2. Copy new SESSION_STRING")
        print("   3. Update env var on Render")
        print("   4. Redeploy")
        print("=" * 60)
    
    port = int(os.environ.get('PORT', 10000))
    print(f"\n🌐 Server running on port {port}...\n")
    app.run(host='0.0.0.0', port=port)
