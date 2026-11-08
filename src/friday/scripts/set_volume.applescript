-- Set the output volume to one of a fixed set of levels or a percentage (0-100).
--
-- The level arrives as an argv parameter: either one of a closed enum
-- ("mute", "low", "medium", "high", "max") or a numeric percentage (0-100),
-- already resolved and validated by slots.py and executor.py.
-- Never a free-spoken number, never interpolated into script text.
on run argv
	set level to item 1 of argv
	set volumeInt to 0
	set isMuted to false

	-- Try to convert to integer first; if it fails, treat as enum
	try
		set volumeInt to level as integer
		if volumeInt is 0 then
			set isMuted to true
		else
			set isMuted to false
		end if
	on error
		-- Treat as enum value
		if level is "mute" then
			set volumeInt to 0
			set isMuted to true
		else if level is "low" then
			set volumeInt to 25
			set isMuted to false
		else if level is "medium" then
			set volumeInt to 50
			set isMuted to false
		else if level is "high" then
			set volumeInt to 75
			set isMuted to false
		else if level is "max" then
			set volumeInt to 100
			set isMuted to false
		else
			error "unknown volume level: " & level
		end if
	end try

	-- Set the volume
	if isMuted then
		set volume output volume volumeInt with output muted
	else
		set volume output volume volumeInt without output muted
	end if
	return "volume set to " & level
end run
