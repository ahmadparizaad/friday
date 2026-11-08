-- Open a URL in a named browser, or in the default browser.
--
-- Both arguments arrive as argv parameters, never interpolated into
-- script text, and are validated against the shortcut and browser tables
-- (shortcuts.py) before we run: the url is always a known host and the
-- browser is always a known app name or the literal "default".
on run argv
	set targetUrl to item 1 of argv
	set targetBrowser to item 2 of argv
	if targetBrowser is "default" then
		open location "https://" & targetUrl
		return "opened " & targetUrl
	end if
	tell application targetBrowser
		activate
		open location "https://" & targetUrl
	end tell
	return "opened " & targetUrl & " in " & targetBrowser
end run
