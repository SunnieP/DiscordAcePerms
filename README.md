# DiscordAcePerms

Gates joining your FiveM server on a Discord **whitelist role**, and keeps ACE permission groups in sync with Discord roles (promote someone in Discord, they get the permissions in-game; demote them, they lose them).

Maintained fork of [DiscordAcePerms](https://github.com/JaredScar/DiscordAcePerms) by Jared Scarito (MIT). Built on [JMS_Discord_API](https://github.com/SunnieP/JMS_Discord_API).

## Setup

1. Install and configure **JMS_Discord_API** first (bot token, guild ID). This resource needs its `GetDiscordRolesEx` export.
2. Edit `config.lua`: set `Discord_Link`, `Whitelist_Role`, and `roleList` (Discord role ID to ACE group). Role IDs are **strings**.
3. Define the groups in your `permissions.cfg`. This resource only adds and removes players from a group; what the group may do is up to you, for example:

```cfg
add_ace group.admin command allow
add_ace group.dev command.restart allow
```

4. In `server.cfg`, start the API before this resource:

```cfg
ensure JMS_Discord_API
ensure DiscordAcePerms
```

Players need the Discord desktop app running when they launch FiveM, or FiveM has no Discord identifier for them.

## How it behaves

| Situation | Result |
| --- | --- |
| In the Discord with the whitelist role | Joins; groups from `roleList` are granted |
| In the Discord, no whitelist role | Rejected with a message pointing at your Discord |
| Not in the Discord | Rejected with your invite link |
| Discord app not running | Rejected with instructions |
| Discord itself not answering | Rejected with "try again in a minute" (the check **fails closed**) |
| Role changes mid-session | Picked up by the periodic sync, or sooner with `/refreshPerms` |
| Discord not answering during a sync | Nothing changes: an outage never grants or strips permissions |
| Player leaves the Discord, or loses the whitelist role | Their groups are removed at the next sync |
| Player disconnects | All their groups are removed |

Set `Require_Whitelist = false` to disable the gate and only sync groups. Set `Sync_Interval = 0` to turn off the periodic sync.

## For other resources

```lua
local groups = exports.DiscordAcePerms:GetPlayerGroups(source)   -- sorted array, e.g. { 'group.admin' }
local status = exports.DiscordAcePerms:RefreshPlayer(source)     -- 'ok' | 'not_in_guild' | 'api_error' | ...

AddEventHandler('DiscordAcePerms:permissionsChanged', function(src, groups, added, removed)
	-- runs on the server whenever a player's groups actually change
end)
```

## What changed from the original

**Removed:** the Badger ad button and links, the version checker pointed at the upstream repo, the "press Play anyway" card and its never-ending loop.

**Fixed:**
- Demotions are now revoked. The original forgot what it had granted before re-adding groups, so a demoted player kept their group after `/refreshPerms` and after disconnecting, until the server restarted.
- A Discord outage no longer tells players "you were not detected in our Discord", and no longer lets a second attempt skip the check.
- Players without a Discord identifier are handled properly (the original's check for this never ran, because an empty string is truthy in Lua), and no longer share one `/refreshPerms` cooldown.
- A player who reconnects before their old session's drop is processed keeps their permissions.
- Bad `roleList` entries are skipped with a console message instead of silently granting nothing.

**Changed:** permissions are applied by diffing against what was granted, the API call is `GetDiscordRolesEx`, the manifest declares `dependency 'JMS_Discord_API'` and uses Lua 5.4.

## Tests

```sh
pip install lupa
python3 tests/run_tests.py
```

Runs the real `server.lua` on Lua 5.4 against mocked FiveM functions and a fake API resource. No server or network needed.

## License

MIT. Original work copyright (c) 2019 Jared; modifications copyright (c) 2026 SunnieP. See [LICENSE](LICENSE).
