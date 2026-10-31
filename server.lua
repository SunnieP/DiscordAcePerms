-- DiscordAcePerms
-- Gates joining on a Discord whitelist role and keeps ACE permission groups in sync with Discord roles.
-- Fork of DiscordAcePerms by Jared Scarito (MIT). Requires the JMS_Discord_API resource.

local RESOURCE = GetCurrentResourceName()
local API = 'JMS_Discord_API'

-- A granted entry for someone who is not online is revoked after this long (covers connections that
-- were granted perms but never made it into the server, so no playerDropped fired).
local STALE_GRACE_MS = 120000
local SWEEP_INTERVAL_MS = 60000
local SYNC_STAGGER_MS = 250 -- gap between players in the periodic sync, keeps Discord rate limits happy

-- ---------------------------------------------------------------------------
-- Logging / helpers
-- ---------------------------------------------------------------------------

local function logInfo(msg) print(('^2[%s]^7 %s'):format(RESOURCE, msg)) end
local function logWarn(msg) print(('^3[%s]^7 %s'):format(RESOURCE, msg)) end
local function logError(msg) print(('^1[%s]^7 %s'):format(RESOURCE, msg)) end
local function logDebug(msg)
	if Config.DebugScript then print(('^5[%s DEBUG]^7 %s'):format(RESOURCE, msg)) end
end

local function now() return GetGameTimer() end

local function isSnowflake(v)
	local s = tostring(v)
	return s:match('^%d+$') ~= nil and #s >= 15
end

local function sortedKeys(set)
	local out = {}
	for k in pairs(set) do out[#out + 1] = k end
	table.sort(out)
	return out
end

local function chat(src, msg)
	TriggerClientEvent('chatMessage', src, '^9[^5DiscordAcePerms^9] ^3' .. msg)
end

-- ---------------------------------------------------------------------------
-- Config validation (bad entries are skipped loudly instead of silently granting nothing)
-- ---------------------------------------------------------------------------

local roleEntries = {} -- { role = "<id>", group = "group.x" }
local whitelistRole = nil

local function loadConfig()
	roleEntries = {}
	for i, entry in ipairs(Config.roleList or {}) do
		local roleId, group = entry[1], entry[2]
		if isSnowflake(roleId) and type(group) == 'string' and group:match('^[%w%._%-]+$') then
			roleEntries[#roleEntries + 1] = { role = tostring(roleId), group = group }
		else
			logError(('roleList entry #%d ignored: needs { "<Discord role ID>", "group.name" } (got %s, %s)')
				:format(i, tostring(roleId), tostring(group)))
		end
	end

	whitelistRole = nil
	if Config.Require_Whitelist then
		if isSnowflake(Config.Whitelist_Role) then
			whitelistRole = tostring(Config.Whitelist_Role)
		else
			logError('Require_Whitelist is on but Whitelist_Role is not a valid role ID: NOBODY will be able to join.')
		end
	end

	logInfo(('Loaded %d role mapping(s); whitelist %s.'):format(
		#roleEntries, whitelistRole and ('role ' .. whitelistRole) or (Config.Require_Whitelist and 'MISCONFIGURED' or 'off')))
end

-- ---------------------------------------------------------------------------
-- Discord lookups
-- ---------------------------------------------------------------------------

local function discordIdOf(src)
	local ident = GetPlayerIdentifierByType(tostring(src), 'discord')
	if ident then return (ident:gsub('^discord:', '')) end
	return nil
end

-- Always returns a table with a .status; never throws (the API resource may be stopped).
local function fetchMember(src, discordId, fresh)
	local ok, result = pcall(function()
		if fresh then exports[API]:ClearCache(discordId) end
		return exports[API]:GetDiscordRolesEx(src)
	end)
	if not ok or type(result) ~= 'table' or not result.status then
		logError(('Could not query %s (is it started before this resource?): %s'):format(API, tostring(result)))
		return { status = 'api_error' }
	end
	return result
end

-- Turns a role list into the set of ACE groups it grants. Returns desiredGroups, hasWhitelist.
local function evaluate(roles)
	local have = {}
	for _, r in ipairs(roles or {}) do have[tostring(r)] = true end

	local hasWhitelist = (not Config.Require_Whitelist) or (whitelistRole ~= nil and have[whitelistRole] == true)
	local desired = {}
	if hasWhitelist then
		for _, e in ipairs(roleEntries) do
			if have[e.role] then desired[e.group] = true end
		end
	end
	return desired, hasWhitelist
end

-- ---------------------------------------------------------------------------
-- Permission state. granted[discordId] = { groups = { [group] = true }, src = n, at = ms }
-- Permissions are always changed by DIFFING against what we granted before, so a role that was
-- removed in Discord is revoked (the original wiped its own tracking and never revoked).
-- ---------------------------------------------------------------------------

local granted = {}
local srcToDiscord = {}
local cooldown = {}

local function principal(verb, discordId, group)
	ExecuteCommand(('%s identifier.discord:%s %s'):format(verb, discordId, group))
end

local function applyGroups(src, discordId, desired, where)
	local entry = granted[discordId]
	if not entry then
		entry = { groups = {} }
		granted[discordId] = entry
	end
	entry.src = src
	entry.at = now()
	srcToDiscord[src] = discordId

	local added, removed = {}, {}
	for _, group in ipairs(sortedKeys(desired)) do
		if not entry.groups[group] then
			principal('add_principal', discordId, group)
			entry.groups[group] = true
			added[#added + 1] = group
		end
	end
	for _, group in ipairs(sortedKeys(entry.groups)) do
		if not desired[group] then
			principal('remove_principal', discordId, group)
			entry.groups[group] = nil
			removed[#removed + 1] = group
		end
	end

	if Config.Print_Perm_Grants_And_Removals then
		for _, g in ipairs(added) do print(('[%s] (%s) Added %s to %s'):format(RESOURCE, where, tostring(GetPlayerName(src)), g)) end
		for _, g in ipairs(removed) do print(('[%s] (%s) Removed %s from %s'):format(RESOURCE, where, tostring(GetPlayerName(src)), g)) end
	end
	if #added > 0 or #removed > 0 then
		TriggerEvent('DiscordAcePerms:permissionsChanged', src, sortedKeys(entry.groups), added, removed)
	end
	return added, removed
end

local function revokeAll(discordId, where)
	local entry = granted[discordId]
	if not entry then return end
	for _, group in ipairs(sortedKeys(entry.groups)) do
		principal('remove_principal', discordId, group)
		if Config.Print_Perm_Grants_And_Removals then
			print(('[%s] (%s) Removed %s from %s'):format(RESOURCE, where, discordId, group))
		end
	end
	if entry.src then srcToDiscord[entry.src] = nil end
	granted[discordId] = nil
	TriggerEvent('DiscordAcePerms:permissionsChanged', entry.src, {}, {}, sortedKeys(entry.groups))
end

-- Re-reads one online player's roles and corrects their groups.
-- Returns 'ok' | 'not_in_guild' | 'no_discord' | 'api_error' | 'config_error' | 'gone'.
-- On api_error/config_error NOTHING changes: an outage must not strip or grant permissions.
local function syncPlayer(src, where, fresh)
	src = tonumber(src)
	local discordId = discordIdOf(src)
	if not discordId then return 'no_discord' end

	local member = fetchMember(src, discordId, fresh)

	-- The player may have left while we waited on Discord. Don't re-grant to a ghost.
	if discordIdOf(src) ~= discordId then return 'gone' end

	if member.status == 'ok' then
		local desired = evaluate(member.roles)
		applyGroups(src, discordId, desired, where)
		if Config.Trigger_vMenu then TriggerEvent('vMenu:RequestPermissions', src) end
		return 'ok'
	elseif member.status == 'not_in_guild' then
		revokeAll(discordId, where) -- they left the Discord: authoritative
		return 'not_in_guild'
	end
	return member.status
end

-- ---------------------------------------------------------------------------
-- Joining
-- ---------------------------------------------------------------------------

local function rejectionText(kind)
	local name = Config.Server_Name or 'this server'
	local link = Config.Discord_Link or ''
	if kind == 'no_discord' then
		return ('%s: we could not find your Discord. Open the Discord desktop app, then fully restart FiveM and try again.'):format(name)
	elseif kind == 'not_in_guild' then
		return ('%s: you are not in our Discord. Join here, get whitelisted, then reconnect: %s'):format(name, link)
	elseif kind == 'not_whitelisted' then
		return ('%s: you are in our Discord but do not have the whitelist role yet. Apply in the Discord: %s'):format(name, link)
	end
	return ('%s: we could not verify your whitelist because Discord did not answer. Please try again in a minute.'):format(name)
end

AddEventHandler('playerConnecting', function(playerName, setKickReason, deferrals)
	local src = source -- capture before anything yields
	deferrals.defer()
	Wait(0)
	deferrals.update('Checking your Discord whitelist...')

	local strict = Config.Require_Whitelist
	local discordId = discordIdOf(src)
	if not discordId then
		if strict then deferrals.done(rejectionText('no_discord')) else deferrals.done() end
		return
	end

	local member = fetchMember(src, discordId, true) -- fresh: someone just whitelisted must get in right away
	if discordIdOf(src) ~= discordId then return end -- they cancelled while we waited

	if member.status == 'ok' then
		local desired, hasWhitelist = evaluate(member.roles)
		if strict and not hasWhitelist then
			deferrals.done(rejectionText('not_whitelisted'))
			return
		end
		applyGroups(src, discordId, desired, 'playerConnecting')
		if Config.Trigger_vMenu then TriggerEvent('vMenu:RequestPermissions', src) end
		deferrals.done()
	elseif member.status == 'not_in_guild' then
		if strict then deferrals.done(rejectionText('not_in_guild')) else deferrals.done() end
	else
		logWarn(('Could not check %s (%s): %s'):format(tostring(playerName), discordId, member.status))
		if strict then deferrals.done(rejectionText('api_error')) else deferrals.done() end
	end
end)

AddEventHandler('playerDropped', function()
	local src = source
	local discordId = discordIdOf(src) or srcToDiscord[src]
	srcToDiscord[src] = nil
	local entry = discordId and granted[discordId]
	-- Only revoke if this is still the session that holds the grant. If they already reconnected
	-- under a new id, the new session owns it.
	if entry and entry.src == src then
		revokeAll(discordId, 'playerDropped')
	end
end)

-- ---------------------------------------------------------------------------
-- /refreshPerms
-- ---------------------------------------------------------------------------

if Config.Allow_Refresh_Command then
	RegisterCommand('refreshPerms', function(src)
		if src == 0 then
			print(('[%s] /refreshPerms must be run by a player in-game.'):format(RESOURCE))
			return
		end
		local discordId = discordIdOf(src)
		if not discordId then
			chat(src, '^1Your Discord was not found. Open the Discord desktop app and restart FiveM.')
			return
		end

		local t = now()
		local untilMs = cooldown[discordId]
		if untilMs and untilMs > t then
			chat(src, ('^1You can refresh again in ^3%d ^1seconds.'):format(math.ceil((untilMs - t) / 1000)))
			return
		end
		cooldown[discordId] = t + (Config.Refresh_Throttle or 60) * 1000 -- set first so double-taps can't stack requests

		local status = syncPlayer(src, 'refreshPerms', true)
		if status == 'ok' then
			chat(src, 'Your permissions have been refreshed ^2successfully^3.')
		elseif status == 'not_in_guild' then
			chat(src, '^1You are no longer in our Discord, so your permissions were removed.')
		else
			cooldown[discordId] = nil -- a failed check shouldn't cost them the cooldown
			chat(src, '^1Could not reach Discord right now, so your permissions were NOT changed. Try again shortly.')
		end
	end)
end

-- ---------------------------------------------------------------------------
-- Background work: periodic sync + cleanup
-- ---------------------------------------------------------------------------

local function sweep()
	local t = now()
	local online = {}
	for _, id in ipairs(GetPlayers()) do
		local d = discordIdOf(id)
		if d then online[d] = true end
	end
	for discordId, entry in pairs(granted) do
		if not online[discordId] and t - entry.at > STALE_GRACE_MS then
			revokeAll(discordId, 'stale')
		end
	end
	for key, expires in pairs(cooldown) do
		if expires <= t then cooldown[key] = nil end
	end
end

CreateThread(function()
	while true do
		Wait(SWEEP_INTERVAL_MS)
		sweep()
	end
end)

CreateThread(function()
	local interval = tonumber(Config.Sync_Interval) or 0
	if interval <= 0 then return end
	while true do
		Wait(interval * 1000)
		for _, id in ipairs(GetPlayers()) do
			syncPlayer(id, 'periodic', false)
			Wait(SYNC_STAGGER_MS)
		end
	end
end)

-- Startup check: do the role IDs in config.lua exist in your Discord? A typo, or an ID that was
-- rounded by a spreadsheet, silently matches nobody, so say so loudly instead.
-- (Limit: roles are looked up by name, so two roles sharing one name can make one look missing.)
CreateThread(function()
	Wait(5000) -- let JMS_Discord_API finish starting and connecting
	local ok, list = pcall(function() return exports[API]:GetGuildRoleList() end)
	if not ok or type(list) ~= 'table' then
		logWarn('Could not fetch your Discord role list, so the role IDs in config.lua were NOT verified.')
		return
	end

	local nameById = {}
	for name, id in pairs(list) do nameById[tostring(id)] = name end

	local function check(label, id)
		if nameById[id] then
			logInfo(('%s: role %s is "%s"'):format(label, id, nameById[id]))
		else
			logError(('%s: role ID %s was not found in your Discord. Check for a typo, or an ID rounded by a spreadsheet. Copy it again with Developer Mode on.'):format(label, id))
		end
	end

	if whitelistRole then check('Whitelist', whitelistRole) end
	for _, e in ipairs(roleEntries) do check(e.group, e.role) end
end)

-- ---------------------------------------------------------------------------
-- Exports for other resources
-- ---------------------------------------------------------------------------

-- Sorted array of the ACE groups this player currently holds through Discord.
exports('GetPlayerGroups', function(src)
	local discordId = discordIdOf(src)
	local entry = discordId and granted[discordId]
	return entry and sortedKeys(entry.groups) or {}
end)

-- Re-reads one player's roles right now. Returns the status string from syncPlayer.
exports('RefreshPlayer', function(src)
	return syncPlayer(src, 'export', true)
end)

loadConfig()
