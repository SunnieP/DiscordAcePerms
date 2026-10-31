Config = {
	Server_Name = 'Lover Lounge RP',
	Discord_Link = 'https://discord.gg/CHANGE_ME', -- shown to players who are not in your Discord

	-- Hard whitelist: a player must be in your Discord AND hold Whitelist_Role to join.
	-- If Discord itself is unreachable, nobody can join (the check fails closed).
	Require_Whitelist = true,
	Whitelist_Role = '1552416185696653382',

	Allow_Refresh_Command = true, -- players can run /refreshPerms after their roles change
	Refresh_Throttle = 60, -- seconds between /refreshPerms uses per player
	Sync_Interval = 300, -- seconds between automatic re-checks of every online player (0 = off)

	Trigger_vMenu = true, -- fire vMenu:RequestPermissions after granting perms (harmless if you don't use vMenu)
	DebugScript = false,
	Print_Perm_Grants_And_Removals = true,

	-- { "<Discord role ID>", "<ACE group>" }
	-- Role IDs are STRINGS. A role can map to several groups; a group can come from several roles.
	-- The groups themselves (and what they may do) are defined in your permissions.cfg.
	roleList = {
		{ '1556173765396668486', 'group.admin' },      -- Admin
		{ '1552837076880334992', 'group.dev' },        -- Dev
		{ '1562831844968497193', 'group.management' }, -- Management
		-- { 'OWNER_ROLE_ID', 'group.owner' },          -- Owner: paste the role ID, then uncomment
	},
}
