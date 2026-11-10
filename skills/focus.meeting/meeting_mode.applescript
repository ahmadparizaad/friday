-- meeting_mode.applescript (skill focus.meeting)
--
-- Usage: osascript meeting_mode.applescript start
--
-- Does, in order:
--   1. Mutes the system output volume.
--   2. Politely quits (quit event, never kill) each RUNNING app in chatApps.
--      Apps that are not running are left alone and are not launched.
--   3. Looks in Calendar for a timed event that is happening now or starts
--      within windowMinutes. It searches that event's url, location and
--      description for the first link whose host is on the allow-list below
--      (https only), and opens that one link with `open location`.
--
-- Never: sets Do Not Disturb (cannot be done reliably by script), kills apps,
-- runs shell commands, or opens any URL other than an allow-listed https
-- meeting link. Calendar text is untrusted: it is only searched for links,
-- never evaluated or run.
--
-- Output: "meeting mode on: muted, quit <apps | no chat apps>, <opened HOST link | no meeting link found | calendar unavailable (...)>"

property chatApps : {"Slack", "WhatsApp", "Discord", "Messages", "Telegram"}
property meetingHosts : {"meet.google.com", "teams.microsoft.com", "zoom.us"}
property meetingHostSuffixes : {".zoom.us"}
property windowMinutes : 15

on run argv
	if (count of argv) is not 1 then error "usage: meeting_mode.applescript start"
	if item 1 of argv is not "start" then error "usage: meeting_mode.applescript start"

	set volume with output muted

	set quitInfo to my quitChatApps()
	set quitText to my quitSummary(quitInfo)
	set linkText to my openMeetingLink()
	set focusText to my turnOnFocus()

	return "meeting mode on: muted, quit " & quitText & ", " & linkText & ", " & focusText
end run

-- Do Not Disturb has no reliable scripting route, but a Shortcut can set a
-- Focus. If the user made one named "Friday Focus On" (Shortcuts > New >
-- Set Focus: Do Not Disturb, On), run it; otherwise skip without failing.
property focusShortcut : "Friday Focus On"

on turnOnFocus()
	try
		tell application "Shortcuts Events"
			if not (exists shortcut focusShortcut) then return "no Focus shortcut"
			run shortcut focusShortcut
		end tell
		return "Do Not Disturb on"
	on error errMsg
		return "Focus shortcut failed (" & errMsg & ")"
	end try
end turnOnFocus

on quitChatApps()
	set quitApps to {}
	set failedApps to {}
	repeat with chatApp in chatApps
		set appName to chatApp as text
		if my isRunning(appName) then
			try
				tell application appName to quit
				set end of quitApps to appName
			on error
				set end of failedApps to appName
			end try
		end if
	end repeat
	return {quitApps, failedApps}
end quitChatApps

on quitSummary(info)
	set quitApps to item 1 of info
	set failedApps to item 2 of info
	set summary to ""
	if (count of quitApps) > 0 then set summary to my joinList(quitApps, ", ")
	if (count of failedApps) > 0 then
		if summary is not "" then set summary to summary & "; "
		set summary to summary & "could not quit " & my joinList(failedApps, ", ")
	end if
	if summary is "" then return "no chat apps"
	return summary
end quitSummary

on isRunning(appName)
	tell application "System Events"
		return (name of every process) contains appName
	end tell
end isRunning

on openMeetingLink()
	set nowDate to current date
	set windowEnd to nowDate + (windowMinutes * minutes)
	try
		set blobs to my calendarTextsInWindow(nowDate, windowEnd)
	on error errMsg
		return "calendar unavailable (" & errMsg & ")"
	end try
	repeat with blob in blobs
		set theLink to my firstAllowedLink(contents of blob)
		if theLink is not "" then
			open location theLink
			return "opened " & my hostOf(theLink) & " link"
		end if
	end repeat
	return "no meeting link found"
end openMeetingLink

on calendarTextsInWindow(nowDate, windowEnd)
	set blobs to {}
	tell application "Calendar"
		repeat with cal in calendars
			set hits to (every event of cal whose allday event is false and start date is less than or equal to windowEnd and end date is greater than or equal to nowDate)
			repeat with ev in hits
				set urlText to ""
				set locText to ""
				set descText to ""
				try
					set urlText to (url of ev) as text
				end try
				try
					set locText to (location of ev) as text
				end try
				try
					set descText to (description of ev) as text
				end try
				set end of blobs to (urlText & linefeed & locText & linefeed & descText)
			end repeat
		end repeat
	end tell
	return blobs
end calendarTextsInWindow

on firstAllowedLink(blob)
	set cleaned to my replaceAll(blob, {tab, linefeed, return, "<", ">", quote, "'", "(", ")", "[", "]", ",", ";"}, " ")
	repeat with tok in my splitText(cleaned, " ")
		set candidate to contents of tok
		if candidate starts with "https://" then
			set candidate to my trimTrailingDots(candidate)
			if my allowedHost(my hostOf(candidate)) then return candidate
		end if
	end repeat
	return ""
end firstAllowedLink

on hostOf(theUrl)
	-- theUrl starts with https:// (checked by the caller)
	if (length of theUrl) < 9 then return ""
	set authority to text 9 thru -1 of theUrl
	set authority to my beforeFirst(authority, "/")
	set authority to my beforeFirst(authority, "?")
	set authority to my beforeFirst(authority, "#")
	if authority contains "@" then return ""
	return my beforeFirst(authority, ":")
end hostOf

on allowedHost(theHost)
	if theHost is "" then return false
	if meetingHosts contains theHost then return true
	repeat with theSuffix in meetingHostSuffixes
		if theHost ends with (theSuffix as text) then return true
	end repeat
	return false
end allowedHost

on trimTrailingDots(theUrl)
	repeat while theUrl ends with "."
		set theUrl to text 1 thru -2 of theUrl
	end repeat
	return theUrl
end trimTrailingDots

on beforeFirst(theText, sep)
	if not (theText contains sep) then return theText
	set AppleScript's text item delimiters to sep
	set parts to text items of theText
	set AppleScript's text item delimiters to ""
	return item 1 of parts
end beforeFirst

on splitText(theText, sep)
	set AppleScript's text item delimiters to sep
	set parts to text items of theText
	set AppleScript's text item delimiters to ""
	return parts
end splitText

on replaceAll(theText, oldList, newText)
	set out to theText
	repeat with oldText in oldList
		set AppleScript's text item delimiters to (oldText as text)
		set parts to text items of out
		set AppleScript's text item delimiters to newText
		set out to parts as text
	end repeat
	set AppleScript's text item delimiters to ""
	return out
end replaceAll

on joinList(theItems, sep)
	set AppleScript's text item delimiters to sep
	set joined to theItems as text
	set AppleScript's text item delimiters to ""
	return joined
end joinList
