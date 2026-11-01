#!/usr/bin/env python3
"""
Regression tests for DiscordAcePerms, run against mocked FiveM natives and a fake JMS_Discord_API.

    pip install lupa
    python3 tests/run_tests.py
"""
import os
import sys
import traceback

# FiveM runs Lua 5.4 (lua54 'yes'), so test on exactly that. Fall back for older lupa builds.
try:
    from lupa.lua54 import LuaRuntime, lua_type
except ImportError:
    from lupa import LuaRuntime, lua_type

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WL = "1552416185696653382"
ADMIN = "1556173765396668486"
DEV = "1552837076880334992"
MGMT = "1562831844968497193"
OWNER = "1552415657503490200"
SWEET = "1552419921344856175"
OTHER = "1500000000000000001"
U1 = "111111111111111111"
U2 = "222222222222222222"


def to_py(v):
    if lua_type(v) == "table":
        keys = list(v.keys())
        if not keys:
            return []
        if all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
            return [to_py(v[i]) for i in range(1, len(keys) + 1)]
        return {str(k): to_py(v[k]) for k in keys}
    return v


def ok(roles):
    return {"status": "ok", "roles": list(roles)}


PRELUDE = r"""
__budget = nil
__api_raises = false
__exports = {}

local function tostr(...) local t = {} for i = 1, select('#', ...) do t[#t+1] = tostring((select(i, ...))) end return table.concat(t, ' ') end
print = function(...) py_print(tostr(...)) end

GetCurrentResourceName = function() return "DiscordAcePerms" end
__ace_ok = true
IsPrincipalAceAllowed = function(p, o) return __ace_ok end
GetGameTimer = function() return py_now() end
GetPlayerName = function(src) return "P" .. tostring(src) end
GetPlayerIdentifierByType = function(src, kind) return py_ident(src, kind) end
GetPlayers = function() return py_players() end
ExecuteCommand = function(c) py_cmd(c) end
TriggerClientEvent = function(...) py_client(...) end
TriggerEvent = function(name, ...) py_event(name, ...) end
CreateThread = function(fn) py_thread(fn) end
AddEventHandler = function(name, fn) py_handler(name, fn) end
RegisterCommand = function(name, fn) py_command(name, fn) end
Wait = function(ms)
  if __budget ~= nil then
    if __budget <= 0 then error('STOP_THREAD') end
    __budget = __budget - 1
  end
  py_wait(ms)
end

exports = setmetatable({}, {
  __call = function(_, name, fn) __exports[name] = fn end,
  __index = function(_, resource)
    return setmetatable({}, { __index = function(_, fn)
      return function(_self, ...)
        if __api_raises then error('No such export ' .. fn .. ' in resource ' .. resource) end
        if fn == 'ClearCache' then py_clear(...) return end
        if fn == 'GetDiscordRolesEx' then return py_api(...) end
        if fn == 'GetGuildRoleList' then return py_guild_roles() end
      end
    end })
  end,
})
"""


TEST_ROLELIST = f"""
Config.Whitelist_Role = "{WL}"
Config.roleList = {{
  {{ "{ADMIN}", "group.admin" }},
  {{ "{DEV}", "group.dev" }},
  {{ "{MGMT}", "group.management" }},
}}
"""


class Env:
    def __init__(self, config_lua="", players=None, api=None, shipped=False):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.g = self.lua.globals()
        self.players = dict(players) if players is not None else {}  # src -> discord id str (or None)
        self.api = dict(api) if api is not None else {}              # discord id -> {"status":..., "roles":[...]}
        self.clock = 0
        self.cmds, self.printed, self.client, self.events = [], [], [], []
        self.cleared, self.handlers, self.commands, self.threads = [], {}, {}, []
        self.on_api = None
        self.guild_roles = None  # None = the role-list call fails; else {role name: role id}

        g = self.g
        g.py_print = lambda s: self.printed.append(s)
        g.py_now = lambda: self.clock
        g.py_ident = self._ident
        g.py_players = lambda: self.lua.table_from([str(k) for k in self.players])
        g.py_cmd = lambda c: self.cmds.append(c)
        g.py_client = lambda *a: self.client.append([to_py(x) for x in a])
        g.py_event = lambda name, *a: self.events.append((name, [to_py(x) for x in a]))
        g.py_thread = lambda fn: self.threads.append(fn)
        g.py_handler = lambda name, fn: self.handlers.__setitem__(name, fn)
        g.py_command = lambda name, fn: self.commands.__setitem__(name, fn)
        g.py_wait = lambda ms: None
        g.py_clear = lambda d: self.cleared.append(d)
        g.py_api = self._api
        g.py_guild_roles = self._guild_roles

        self.lua.execute(PRELUDE)
        self.lua.execute(open(os.path.join(REPO, "config.lua")).read())
        if not shipped:
            # Tests use their own role list so they do not break when the real config is edited.
            self.lua.execute(TEST_ROLELIST)
        if config_lua:
            self.lua.execute(config_lua)
        self.lua.execute(open(os.path.join(REPO, "server.lua")).read())
        self.exports = self.g["__exports"]

    # --- mocks ------------------------------------------------------------
    def _ident(self, src, kind):
        if kind != "discord":
            return None
        did = self.players.get(int(src))
        return f"discord:{did}" if did else None

    def _guild_roles(self):
        if self.guild_roles is None:
            self.g["__api_raises"] = True
            raise RuntimeError("guild role list unavailable")
        return self.lua.table_from(self.guild_roles)

    def _api(self, src):
        if self.on_api:
            self.on_api(int(src))
        did = self.players.get(int(src))
        res = self.api.get(did, {"status": "not_in_guild"}) if did else {"status": "no_discord"}
        t = {"status": res["status"]}
        if "roles" in res:
            t["roles"] = self.lua.table_from(res["roles"])
        return self.lua.table_from(t)

    # --- drivers ----------------------------------------------------------
    def connect(self, src):
        rec = {"done": [], "updates": [], "deferred": 0}
        mk = self.lua.eval("function(a, b, c) return { defer = a, update = b, done = c } end")
        d = mk(lambda: rec.__setitem__("deferred", rec["deferred"] + 1),
               lambda m=None: rec["updates"].append(m),
               lambda m=None: rec["done"].append(m))
        self.g["source"] = src
        self.handlers["playerConnecting"]("name", lambda *a: None, d)
        return rec

    def drop(self, src, remove=True):
        self.g["source"] = src
        self.handlers["playerDropped"]("quit")
        if remove:
            self.players.pop(src, None)

    def refresh(self, src):
        self.commands["refreshPerms"](src, self.lua.table_from([]), "refreshPerms")

    def run_thread(self, index, budget):
        self.g["__budget"] = budget
        try:
            self.threads[index]()
        except Exception as e:
            if "STOP_THREAD" not in str(e):
                raise
        finally:
            self.g["__budget"] = None

    def effective(self, discord_id):
        groups = set()
        for c in self.cmds:
            verb, ident, group = c.split()
            if ident != f"identifier.discord:{discord_id}":
                continue
            (groups.add if verb == "add_principal" else groups.discard)(group)
        return sorted(groups)

    def last_chat(self):
        return self.client[-1][-1] if self.client else ""

    def output(self):
        return "\n".join(self.printed)


RESULTS = []


def test(fn):
    try:
        fn()
        RESULTS.append((fn.__name__, True, ""))
    except AssertionError as e:
        RESULTS.append((fn.__name__, False, str(e) or "assertion failed"))
    except Exception:
        RESULTS.append((fn.__name__, False, traceback.format_exc(limit=4)))
    return fn


def one(roles=None, api=None, config_lua=""):
    """Env with a single player (src 1, Discord U1)."""
    return Env(config_lua=config_lua, players={1: U1}, api={U1: api if api is not None else ok(roles or [])})


# ---------------------------------------------------------------------------
# Joining / whitelist
# ---------------------------------------------------------------------------

@test
def whitelisted_staff_joins_and_gets_groups():
    e = one([WL, ADMIN, DEV])
    rec = e.connect(1)
    assert rec["deferred"] == 1 and rec["done"] == [None], rec
    assert e.effective(U1) == ["group.admin", "group.dev"], e.effective(U1)
    assert to_py(e.exports["GetPlayerGroups"](1)) == ["group.admin", "group.dev"]


@test
def whitelisted_player_without_staff_roles_joins_with_no_groups():
    e = one([WL])
    assert e.connect(1)["done"] == [None]
    assert e.effective(U1) == []


@test
def missing_whitelist_role_is_rejected():
    e = one([ADMIN, DEV])  # staff roles but NOT whitelisted
    rec = e.connect(1)
    assert len(rec["done"]) == 1 and "whitelist role" in rec["done"][0], rec
    assert e.effective(U1) == [], "a rejected player must get no permissions"


@test
def not_in_discord_is_rejected_with_the_invite():
    e = Env(players={1: U1}, api={})  # unknown to the fake guild -> not_in_guild
    rec = e.connect(1)
    assert "not in our Discord" in rec["done"][0] and "discord.gg" in rec["done"][0], rec


@test
def no_discord_app_is_rejected_with_instructions():
    e = Env(players={1: None})
    rec = e.connect(1)
    assert "desktop app" in rec["done"][0], rec
    assert not e.cmds


@test
def discord_outage_fails_closed_without_claiming_not_in_discord():
    """Original bug: an API failure showed 'You were not detected in our Discord'."""
    e = one(api={"status": "api_error"})
    rec = e.connect(1)
    msg = rec["done"][0]
    assert "did not answer" in msg and "not in our Discord" not in msg, msg
    assert e.effective(U1) == []


@test
def one_discord_hiccup_while_joining_is_retried():
    e = one([WL])
    calls = []
    real = {"status": "ok", "roles": [WL]}
    def flip(_src):
        calls.append(1)
        e.api[U1] = {"status": "api_error"} if len(calls) == 1 else real
    e.on_api = flip
    rec = e.connect(1)
    assert rec["done"] == [None], rec
    assert len(calls) == 2 and any("Trying again" in (m or "") for m in rec["updates"]), (calls, rec)


@test
def a_lasting_outage_still_rejects_after_the_retries():
    e = one(api={"status": "api_error"})
    calls = []
    e.on_api = lambda _s: calls.append(1)
    rec = e.connect(1)
    assert len(calls) == 3 and "did not answer" in rec["done"][0], (len(calls), rec)


@test
def api_resource_not_running_is_handled():
    e = one([WL])
    e.g["__api_raises"] = True
    rec = e.connect(1)
    assert len(rec["done"]) == 1 and "did not answer" in rec["done"][0]
    assert "JMS_Discord_API" in e.output()


@test
def connect_forces_a_fresh_role_check():
    """A player who was just whitelisted must not wait out the cache."""
    e = one([WL])
    e.connect(1)
    assert e.cleared == [U1], e.cleared


@test
def whitelist_off_lets_everyone_in():
    e = Env(config_lua="Config.Require_Whitelist = false", players={1: U1, 2: U2, 3: None},
            api={U1: {"status": "api_error"}})
    assert e.connect(1)["done"] == [None]   # outage
    assert e.connect(2)["done"] == [None]   # not in guild
    assert e.connect(3)["done"] == [None]   # no discord
    assert not e.cmds


@test
def misconfigured_whitelist_role_fails_closed_and_says_so():
    e = Env(config_lua='Config.Whitelist_Role = "CHANGE_ME"', players={1: U1}, api={U1: ok([WL, ADMIN])})
    assert "NOBODY will be able to join" in e.output()
    rec = e.connect(1)
    assert rec["done"][0] is not None, "must reject, never wave through"


@test
def player_who_cancels_during_the_check_is_not_granted_anything():
    e = one([WL, ADMIN])
    e.on_api = lambda src: e.players.pop(src, None)  # they quit while Discord was answering
    rec = e.connect(1)
    assert rec["done"] == [], rec
    assert e.effective(U1) == []


# ---------------------------------------------------------------------------
# Permission sync / revocation
# ---------------------------------------------------------------------------

@test
def demotion_is_revoked_on_refresh_and_nothing_survives_disconnect():
    """Original bug (critical): a demoted player kept their group after /refreshPerms and after disconnect."""
    e = one([WL, ADMIN, DEV])
    e.connect(1)
    assert e.effective(U1) == ["group.admin", "group.dev"]
    e.api[U1] = ok([WL, ADMIN])           # Dev role removed in Discord
    e.refresh(1)
    assert e.effective(U1) == ["group.admin"], e.effective(U1)
    e.drop(1)
    assert e.effective(U1) == [], "no permission may outlive the session"


@test
def losing_the_whitelist_role_removes_permissions_on_sync():
    e = one([WL, ADMIN])
    e.connect(1)
    e.api[U1] = ok([ADMIN])               # whitelist taken away, staff role still there
    e.refresh(1)
    assert e.effective(U1) == []


@test
def leaving_the_discord_revokes_everything():
    e = one([WL, ADMIN])
    e.connect(1)
    e.api[U1] = {"status": "not_in_guild"}
    e.refresh(1)
    assert e.effective(U1) == []
    assert "no longer in our Discord" in e.last_chat()


@test
def discord_outage_during_sync_changes_nothing():
    e = one([WL, ADMIN])
    e.connect(1)
    e.api[U1] = {"status": "api_error"}
    e.refresh(1)
    assert e.effective(U1) == ["group.admin"], "an outage must not strip permissions"
    assert "NOT changed" in e.last_chat()


@test
def promotion_is_granted_on_refresh():
    e = one([WL])
    e.connect(1)
    e.api[U1] = ok([WL, MGMT])
    e.refresh(1)
    assert e.effective(U1) == ["group.management"]


@test
def several_roles_for_the_same_group_grant_it_once():
    cfg = f'Config.roleList = {{ {{"{ADMIN}","group.staff"}}, {{"{DEV}","group.staff"}} }}'
    e = one([WL, ADMIN, DEV], config_lua=cfg)
    e.connect(1)
    assert [c for c in e.cmds if c.startswith("add_principal")] == [f"add_principal identifier.discord:{U1} group.staff"]


@test
def old_session_dropping_after_a_reconnect_does_not_strip_the_new_session():
    e = Env(players={1: U1}, api={U1: ok([WL, ADMIN])})
    e.connect(1)
    e.players[2] = U1                      # same person reconnects under a new id before the old drop lands
    e.connect(2)
    e.drop(1, remove=False)
    assert e.effective(U1) == ["group.admin"], e.effective(U1)


@test
def refresh_events_report_what_changed():
    e = one([WL, ADMIN, DEV])
    e.connect(1)
    e.events.clear()
    e.api[U1] = ok([WL, ADMIN])
    e.refresh(1)
    changed = [a for n, a in e.events if n == "DiscordAcePerms:permissionsChanged"]
    assert changed and changed[-1][1] == ["group.admin"] and changed[-1][3] == ["group.dev"], changed


@test
def vmenu_event_is_optional():
    e = one([WL, ADMIN])
    e.connect(1)
    assert any(n == "vMenu:RequestPermissions" for n, _ in e.events)
    e2 = one([WL, ADMIN], config_lua="Config.Trigger_vMenu = false")
    e2.connect(1)
    assert not any(n == "vMenu:RequestPermissions" for n, _ in e2.events)


# ---------------------------------------------------------------------------
# /refreshPerms
# ---------------------------------------------------------------------------

@test
def refresh_has_a_per_player_cooldown():
    e = one([WL, ADMIN])
    e.connect(1)
    e.refresh(1)
    assert "successfully" in e.last_chat()
    e.refresh(1)
    assert "seconds" in e.last_chat() and "successfully" not in e.last_chat()
    e.clock += 61 * 1000
    e.refresh(1)
    assert "successfully" in e.last_chat()


@test
def players_without_discord_do_not_share_a_cooldown():
    """Original bug: every Discord-less player shared the '' cooldown key."""
    e = Env(players={4: None, 5: None})
    e.refresh(4)
    e.refresh(5)
    for _ in range(2):
        assert "Discord was not found" in e.client[-1 - _][-1], e.client


@test
def failed_refresh_does_not_burn_the_cooldown():
    e = one([WL, ADMIN])
    e.connect(1)
    e.api[U1] = {"status": "api_error"}
    e.refresh(1)
    e.api[U1] = ok([WL, ADMIN, DEV])
    e.refresh(1)
    assert "successfully" in e.last_chat() and e.effective(U1) == ["group.admin", "group.dev"]


@test
def refresh_from_console_does_not_crash():
    e = Env(players={})
    e.refresh(0)
    assert "in-game" in e.output()


# ---------------------------------------------------------------------------
# Background threads
# ---------------------------------------------------------------------------

@test
def periodic_sync_corrects_every_online_player():
    e = Env(players={1: U1, 2: U2}, api={U1: ok([WL, ADMIN]), U2: ok([WL, DEV])})
    e.connect(1)
    e.connect(2)
    e.api[U1] = ok([WL])                  # demoted
    e.api[U2] = ok([WL, DEV, MGMT])       # promoted
    e.run_thread(1, budget=3)             # one interval wait + a stagger wait per player
    assert e.effective(U1) == [], e.effective(U1)
    assert e.effective(U2) == ["group.dev", "group.management"], e.effective(U2)


@test
def periodic_sync_can_be_turned_off():
    e = one([WL, ADMIN], config_lua="Config.Sync_Interval = 0")
    e.connect(1)
    e.api[U1] = ok([WL])
    e.run_thread(1, budget=5)
    assert e.effective(U1) == ["group.admin"], "interval 0 must disable the loop"


@test
def stale_grants_are_swept_only_after_the_grace_period():
    e = one([WL, ADMIN])
    e.connect(1)
    e.players.pop(1)                       # never made it in: no playerDropped fired
    e.clock = 60 * 1000
    e.run_thread(0, budget=1)
    assert e.effective(U1) == ["group.admin"], "inside the grace period it must survive (may still be loading)"
    e.clock = 130 * 1000
    e.run_thread(0, budget=1)
    assert e.effective(U1) == [], "past the grace period it must be revoked"


@test
def online_players_are_never_swept():
    e = one([WL, ADMIN])
    e.connect(1)
    e.clock = 10 * 60 * 1000
    e.run_thread(0, budget=1)
    assert e.effective(U1) == ["group.admin"]


# ---------------------------------------------------------------------------
# Config / hygiene
# ---------------------------------------------------------------------------

@test
def bad_config_entries_are_skipped_loudly_and_good_ones_still_work():
    cfg = f'Config.roleList = {{ {{"OWNER_ROLE_ID","group.owner"}}, {{"{ADMIN}","group.admin now"}}, {{"{DEV}","group.dev"}} }}'
    e = one([WL, ADMIN, DEV], config_lua=cfg)
    assert e.output().count("ignored") == 2, e.output()
    e.connect(1)
    assert e.effective(U1) == ["group.dev"]


@test
def numeric_role_ids_in_config_still_match():
    e = one([WL, ADMIN], config_lua=f"Config.roleList = {{ {{ {ADMIN}, 'group.admin' }} }}")
    e.connect(1)
    assert e.effective(U1) == ["group.admin"]


@test
def exports_work():
    e = one([WL, ADMIN])
    e.connect(1)
    e.api[U1] = ok([WL, ADMIN, MGMT])
    assert e.exports["RefreshPlayer"](1) == "ok"
    assert to_py(e.exports["GetPlayerGroups"](1)) == ["group.admin", "group.management"]
    assert to_py(e.exports["GetPlayerGroups"](99)) == []


# ---------------------------------------------------------------------------
# The shipped config.lua (what Sunnie actually runs)
# ---------------------------------------------------------------------------

@test
def shipped_config_loads_without_ignored_entries():
    e = Env(players={}, shipped=True)
    assert "ignored" not in e.output() and "MISCONFIGURED" not in e.output(), e.output()
    assert "Loaded 4 role mapping(s)" in e.output(), e.output()


@test
def shipped_config_every_staff_role_gets_the_same_single_group():
    """Staff are deliberately identical during testing; split them up later."""
    for name, role in (("owner", OWNER), ("admin", ADMIN), ("dev", DEV)):
        e = Env(players={1: U1}, api={U1: ok([WL, role])}, shipped=True)
        assert e.connect(1)["done"] == [None], name
        assert e.effective(U1) == ["group.staff"], (name, e.effective(U1))


@test
def shipped_config_holding_several_staff_roles_grants_the_group_once():
    e = Env(players={1: U1}, api={U1: ok([WL, OWNER, ADMIN, DEV, MGMT])}, shipped=True)
    e.connect(1)
    assert [c for c in e.cmds if c.startswith("add_principal")] == [f"add_principal identifier.discord:{U1} group.staff"]


@test
def shipped_config_ordinary_whitelisted_player_gets_no_groups():
    e = Env(players={1: U1}, api={U1: ok([WL])}, shipped=True)
    assert e.connect(1)["done"] == [None]
    assert e.effective(U1) == []


@test
def shipped_config_staff_role_without_whitelist_is_still_rejected():
    e = Env(players={1: U1}, api={U1: ok([OWNER])}, shipped=True)
    assert "whitelist role" in e.connect(1)["done"][0]
    assert e.effective(U1) == []


@test
def shipped_config_demoted_staff_lose_the_group():
    e = Env(players={1: U1}, api={U1: ok([WL, ADMIN])}, shipped=True)
    e.connect(1)
    e.api[U1] = ok([WL])
    e.refresh(1)
    assert e.effective(U1) == []


# ---------------------------------------------------------------------------
# Startup check of configured role IDs
# ---------------------------------------------------------------------------

REAL_ROLES = {"Whitelisted": WL, "Admin": ADMIN, "Dev": DEV, "Management": MGMT, "Sweethearts": SWEET}


@test
def startup_check_confirms_ids_that_exist():
    e = Env(players={}, shipped=True)
    e.guild_roles = dict(REAL_ROLES, Owner=OWNER)
    e.run_thread(2, budget=1)
    out = e.output()
    assert "not found" not in out, out
    assert 'Whitelist: role %s is "Whitelisted"' % WL in out, out


@test
def startup_check_flags_an_id_that_is_not_in_the_discord():
    """The case that motivated it: a mistyped or spreadsheet-rounded ID silently matches nobody."""
    e = Env(players={}, shipped=True)
    e.guild_roles = dict(REAL_ROLES, Owner="1552415657503490304")  # the real owner role, different digits
    e.run_thread(2, budget=1)
    out = e.output()
    assert f"role ID {OWNER} was not found" in out, out
    assert out.count("was not found") == 1, "only the bad ID may be flagged"


@test
def startup_check_flags_a_bad_whitelist_role_too():
    e = Env(players={}, shipped=True)
    e.guild_roles = {"Admin": ADMIN, "Dev": DEV, "Management": MGMT, "Owner": OWNER, "Sweethearts": SWEET}  # whitelist role missing
    e.run_thread(2, budget=1)
    assert f"Whitelist: role ID {WL} was not found" in e.output(), e.output()


@test
def missing_command_permission_is_called_out_at_startup():
    e = Env(players={}, shipped=True)
    e.g["__ace_ok"] = False
    e.run_thread(2, budget=1)
    out = e.output()
    assert "add_ace resource.DiscordAcePerms command.add_principal allow" in out, out
    assert "add_ace resource.DiscordAcePerms command.remove_principal allow" in out, out
    e = Env(players={}, shipped=True)
    e.run_thread(2, budget=1)
    assert "MISSING PERMISSION" not in e.output()


@test
def startup_check_survives_the_api_being_unavailable():
    e = Env(players={}, shipped=True)
    e.guild_roles = None
    e.run_thread(2, budget=1)
    assert "NOT verified" in e.output(), e.output()


@test
def no_upstream_advertising_or_stale_references_left():
    src = ""
    for f in ("server.lua", "config.lua", "fxmanifest.lua"):
        src += open(os.path.join(REPO, f)).read().lower()
    for needle in ("badger", "gyazo", "wjb5vfz", "presentcard", "badger_discord_api", "versionchecker"):
        assert needle not in src, f"found leftover: {needle}"
    assert not os.path.exists(os.path.join(REPO, "versionChecker.lua"))


@test
def manifest_declares_the_api_dependency():
    m = open(os.path.join(REPO, "fxmanifest.lua")).read()
    assert "dependency 'JMS_Discord_API'" in m and "lua54 'yes'" in m


if __name__ == "__main__":
    width = max(len(n) for n, _, _ in RESULTS)
    failed = 0
    for name, passed, detail in RESULTS:
        print(f"{'PASS' if passed else 'FAIL'}  {name.ljust(width)}" + ("" if passed else f"\n      {detail}"))
        failed += 0 if passed else 1
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} passed on {LuaRuntime().eval('_VERSION')}")
    sys.exit(1 if failed else 0)
