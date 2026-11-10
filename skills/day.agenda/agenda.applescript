-- day.agenda: speak today's calendar events and the reminders due today.
--
-- argv contract: exactly ["today"]. Anything else is refused.
-- What it reads:
--   Calendar: events of every calendar whose start date falls in today
--             (one `whose` query per calendar, never a scan of all events).
--   Reminders: incomplete reminders due before tomorrow, overdue included.
-- What it never does: create, edit, move, complete or delete anything;
-- run a shell; or touch any app other than Calendar and Reminders.
-- Output: one speakable paragraph that starts with "You have" or
-- "Your calendar is clear". Lists are capped at 8 events and 5 reminders.

on run argv
  if (count of argv) is not 1 then error "day.agenda expects exactly one argument: today"
  if item 1 of argv is not "today" then error "day.agenda only accepts the argument: today"

  -- today 00:00 up to tomorrow 00:00. Adding 25 hours and then resetting
  -- the time to midnight lands on the next day's midnight across DST changes.
  set dayStart to current date
  set time of dayStart to 0
  set dayEnd to dayStart + (25 * hours)
  set time of dayEnd to 0

  set eventRecs to {}
  tell application "Calendar"
    repeat with cal in calendars
      set found to (every event of cal whose start date >= dayStart and start date < dayEnd)
      repeat with ev in found
        set end of eventRecs to {startDate:(start date of ev), title:(summary of ev), isAllDay:(allday event of ev)}
      end repeat
    end repeat
  end tell

  set reminderNames to {}
  tell application "Reminders"
    repeat with lst in lists
      set reminderNames to reminderNames & (name of (every reminder of lst whose completed is false and due date is not missing value and due date < dayEnd))
    end repeat
  end tell

  set sortedEvents to my sortByStart(eventRecs)

  set eventPhrases to {}
  repeat with ev in sortedEvents
    set end of eventPhrases to my eventPhrase(ev)
  end repeat

  set cleanReminders to {}
  repeat with rn in reminderNames
    set end of cleanReminders to my cleanText(rn)
  end repeat

  set eventCount to count of eventPhrases
  set reminderCount to count of cleanReminders

  if eventCount = 0 and reminderCount = 0 then return "Your calendar is clear today and no reminders are due."

  set reminderSentence to ""
  if reminderCount > 0 then
    set remNoun to "reminders"
    if reminderCount = 1 then set remNoun to "reminder"
    set reminderSentence to reminderCount & " " & remNoun & " due: " & my joinCapped(cleanReminders, 5) & "."
  end if

  if eventCount = 0 then return "Your calendar is clear today. " & reminderSentence

  set evNoun to "events"
  if eventCount = 1 then set evNoun to "event"
  set eventSentence to "You have " & eventCount & " " & evNoun & " today: " & my joinCapped(eventPhrases, 8) & "."

  if reminderCount = 0 then return eventSentence & " No reminders are due."
  return eventSentence & " " & reminderSentence
end run

-- Sorts event records by start date (insertion sort; lists are small).
on sortByStart(evs)
  set sortedList to {}
  repeat with ev in evs
    set placed to false
    set newList to {}
    repeat with cur in sortedList
      if (not placed) and (startDate of cur) > (startDate of ev) then
        set end of newList to ev
        set placed to true
      end if
      set end of newList to cur
    end repeat
    if not placed then set end of newList to ev
    set sortedList to newList
  end repeat
  return sortedList
end sortByStart

on eventPhrase(ev)
  set t to my cleanText(title of ev)
  if t is "" then set t to "untitled event"
  if isAllDay of ev then return "all day " & t
  set d to startDate of ev
  return my pad2(hours of d) & ":" & my pad2(minutes of d) & " " & t
end eventPhrase

on pad2(n)
  if n < 10 then return "0" & n
  return n as text
end pad2

-- Joins up to `cap` phrases with ", " and appends ", and N more" for the rest.
on joinCapped(phrases, cap)
  set total to count of phrases
  set shown to phrases
  set extra to 0
  if total > cap then
    set shown to items 1 thru cap of phrases
    set extra to total - cap
  end if
  set joined to my joinList(shown, ", ")
  if extra > 0 then set joined to joined & ", and " & extra & " more"
  return joined
end joinCapped

on joinList(parts, sep)
  set AppleScript's text item delimiters to sep
  set out to parts as text
  set AppleScript's text item delimiters to ""
  return out
end joinList

-- Titles are user data: line breaks and tabs become spaces, and the
-- result is trimmed. A missing title becomes "".
on cleanText(t)
  if t is missing value then return ""
  set s to t as text
  set s to my replaceText(s, return, " ")
  set s to my replaceText(s, linefeed, " ")
  set s to my replaceText(s, tab, " ")
  repeat while (length of s) > 0 and character 1 of s is " "
    set s to text 2 thru -1 of s
  end repeat
  repeat while (length of s) > 0 and character -1 of s is " "
    set s to text 1 thru -2 of s
  end repeat
  return s
end cleanText

on replaceText(src, findStr, replStr)
  set AppleScript's text item delimiters to findStr
  set parts to text items of src
  set AppleScript's text item delimiters to replStr
  set out to parts as text
  set AppleScript's text item delimiters to ""
  return out
end replaceText
