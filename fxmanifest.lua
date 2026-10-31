fx_version 'cerulean'
game 'gta5'
lua54 'yes'

name 'DiscordAcePerms'
author 'SunnieP (fork of DiscordAcePerms by Jared Scarito)'
description 'Gate joins on a Discord whitelist role and sync Discord roles to ACE permission groups'
version '2.0.0'
repository 'https://github.com/SunnieP/DiscordAcePerms'

-- Must start AFTER JMS_Discord_API, otherwise early connections get no permissions.
dependency 'JMS_Discord_API'

server_scripts {
	'config.lua',
	'server.lua',
}
