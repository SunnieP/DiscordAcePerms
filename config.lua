Config = {
	Server_Name = 'Lover Lounge RP',
	Discord_Link = 'https://discord.gg/loverlounge', -- shown to players who are not in your Discord

	-- Hard whitelist: a player must be in your Discord AND hold Whitelist_Role to join.
	-- If Discord itself is unreachable, nobody can join (the check fails closed).
	Require_Whitelist = true,
	Whitelist_Role = '1552416185696653382',

	Allow_Refresh_Command = true, -- players can run /refreshPerms after their roles change
	Refresh_Throttle = 60, -- seconds between /refreshPerms uses per player
	Sync_Interval = 300, -- seconds between automatic re-checks of every online player (0 = off)
	Connect_Retries = 2, -- extra tries when Discord fails to answer while a player is joining (0 = none)

	Trigger_vMenu = true, -- fire vMenu:RequestPermissions after granting perms (harmless if you don't use vMenu)
	DebugScript = false,
	Print_Perm_Grants_And_Removals = true,

	-- { "<Discord role ID>", "<ACE group>" }
	-- Role IDs are STRINGS. A role can map to several groups; a group can come from several roles.
	-- The groups themselves (and what they may do) are defined in your permissions.cfg.
	-- On startup the console tells you if any ID here (or Whitelist_Role) is not found in your Discord.
	roleList = {
		-- Staff: during testing every staff role gets the same group (group.staff, which server.cfg makes inherit
		-- everything group.admin can do). Split them up later by pointing each role at its own group.
		{ '1552415657503490200', 'group.staff' }, -- Owner
		{ '1556173765396668486', 'group.staff' }, -- Admin
		{ '1552837076880334992', 'group.staff' }, -- Dev
		-- { 'MANAGEMENT_ROLE_ID', 'group.staff' }, -- Management: off until the correct role ID is confirmed

		-- Gated city features. Right now the only one is the Baddies Portal (Discord role: Sweethearts).
		{ '1552419921344856175', 'group.sweethearts' }, -- Sweethearts
	},
}
