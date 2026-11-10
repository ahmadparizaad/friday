-- add_reminder.applescript (reminders.add)
--
-- Creates ONE reminder in the default list of the Reminders app.
--
-- argv: item 1 = task text, item 2 = time phrase.
--   Time phrases: "5", "5 pm", "5pm", "5:30 pm", "17:00", "noon", optionally
--   prefixed by "today" or "tomorrow" (and an optional "at").
--   A bare hour 1-6 means pm. A bare hour 7-11 means its next occurrence
--   (am or pm, whichever comes first). With no day given, a time already
--   past today moves to tomorrow. Anything else is refused.
--
-- Never: reads, edits, moves or deletes an existing reminder; touches other
-- lists; runs a shell; builds code from the arguments.
--
-- An empty time phrase (the skill's default) adds a reminder with no due
-- date; "today"/"tomorrow" alone adds an all-day reminder on that day.
--
-- Success output: "reminder set: <task> at <h:mm am/pm> <today|tomorrow>"
-- Failure: exits non-zero with "I didn't understand the time <when>", or a
-- clear message for a missing task or a time already past today.

on run argv
  if (count of argv) is not 2 then error "add_reminder needs a task and a time"
  set taskText to my cleanText(item 1 of argv)
  set whenText to my cleanText(item 2 of argv)
  if taskText is "" then error "I need something to remind you about"
  -- no time ("remind me to buy milk"): a reminder with no due date;
  -- a day alone ("... tomorrow"): an all-day reminder on that day.
  if whenText is in {"", "today", "tomorrow"} then
    set offsetDays to 0
    if whenText is "tomorrow" then set offsetDays to 1
    tell application "Reminders"
      set targetList to default list
      if whenText is "" then
        tell targetList to make new reminder with properties {name:taskText}
      else
        set dayStart to (current date) + offsetDays * days
        set time of dayStart to 0
        tell targetList to make new reminder with properties {name:taskText, allday due date:dayStart}
      end if
    end tell
    if whenText is "" then return "reminder set: " & taskText & " (no time)"
    return "reminder set: " & taskText & " " & whenText
  end if
  set spec to my parseWhen(whenText)
  set info to my dueFor(spec, current date)
  set dueDate to due of info
  set dayLabel to "today"
  if isTomorrow of info then set dayLabel to "tomorrow"
  set clockText to my clockLabel(dueDate)
  tell application "Reminders"
    set targetList to default list
    tell targetList to make new reminder with properties {name:taskText, due date:dueDate}
  end tell
  return "reminder set: " & taskText & " at " & clockText & " " & dayLabel
end run

-- Parse the time phrase into a 24-hour clock value, or refuse.
on parseWhen(whenText)
  set refuse to "I didn't understand the time " & whenText
  set theWords to my splitWords(whenText)
  set dayWord to ""
  if theWords is not {} and (item 1 of theWords) is in {"today", "tomorrow"} then
    set dayWord to item 1 of theWords
    set theWords to my wordsFrom(theWords, 2)
  end if
  if theWords is not {} and (item 1 of theWords) is "at" then
    set theWords to my wordsFrom(theWords, 2)
  end if
  if theWords is {} then error refuse
  if (item 1 of theWords) is "noon" then
    if (count of theWords) is not 1 then error refuse
    return {dayWord:dayWord, hrs:12, mins:0, ambiguous:false}
  end if
  if (count of theWords) > 2 then error refuse

  set tokenText to item 1 of theWords
  set ampm to ""
  if (count of theWords) is 2 then
    set ampm to item 2 of theWords
    if ampm is not "am" and ampm is not "pm" then error refuse
  else if (count of tokenText) > 2 and ((text -2 thru -1 of tokenText) is "am" or (text -2 thru -1 of tokenText) is "pm") then
    set ampm to text -2 thru -1 of tokenText
    set tokenText to text 1 thru -3 of tokenText
  end if

  set hourText to tokenText
  set minuteText to "0"
  set colonAt to offset of ":" in tokenText
  if colonAt > 0 then
    set hourText to ""
    if colonAt > 1 then set hourText to text 1 thru (colonAt - 1) of tokenText
    set minuteText to ""
    if colonAt < (count of tokenText) then set minuteText to text (colonAt + 1) thru -1 of tokenText
    if (count of minuteText) is not 2 or not my isDigits(minuteText) then error refuse
  end if
  if not my isDigits(hourText) or (count of hourText) > 2 then error refuse
  set hourN to hourText as integer
  set minN to minuteText as integer
  if minN > 59 then error refuse

  set hrs to hourN
  set ambiguous to false
  if ampm is "am" or ampm is "pm" then
    if hourN < 1 or hourN > 12 then error refuse
    if ampm is "am" then
      set hrs to hourN mod 12
    else
      set hrs to (hourN mod 12) + 12
    end if
  else
    if hourN > 23 then error refuse
    if hourN = 12 then
      set hrs to 12
    else if hourN >= 1 and hourN <= 6 then
      set hrs to hourN + 12
    else if hourN >= 7 and hourN <= 11 then
      set ambiguous to true
    end if
  end if
  return {dayWord:dayWord, hrs:hrs, mins:minN, ambiguous:ambiguous}
end parseWhen

-- Work out the due date from the parsed phrase and the current time.
on dueFor(spec, nowDate)
  set dayWord to dayWord of spec
  set hrs to hrs of spec
  set mins to mins of spec
  if ambiguous of spec then
    if dayWord is "tomorrow" then return {due:my atTime(nowDate, 1, hrs, mins), isTomorrow:true}
    set amDue to my atTime(nowDate, 0, hrs, mins)
    set pmDue to my atTime(nowDate, 0, hrs + 12, mins)
    if amDue > nowDate then return {due:amDue, isTomorrow:false}
    if pmDue > nowDate then return {due:pmDue, isTomorrow:false}
    if dayWord is "today" then error "that time has already passed today"
    return {due:my atTime(nowDate, 1, hrs, mins), isTomorrow:true}
  end if
  if dayWord is "tomorrow" then return {due:my atTime(nowDate, 1, hrs, mins), isTomorrow:true}
  set todayDue to my atTime(nowDate, 0, hrs, mins)
  if todayDue > nowDate then return {due:todayDue, isTomorrow:false}
  if dayWord is "today" then error "that time has already passed today"
  return {due:my atTime(nowDate, 1, hrs, mins), isTomorrow:true}
end dueFor

on atTime(baseDate, dayOffset, hrs, mins)
  set d to baseDate + (dayOffset * days)
  set time of d to ((hrs * 3600) + (mins * 60))
  return d
end atTime

on clockLabel(d)
  set h24 to hours of d
  set m to minutes of d
  set h12 to h24 mod 12
  if h12 = 0 then set h12 to 12
  set ap to "am"
  if h24 >= 12 then set ap to "pm"
  set mm to m as text
  if m < 10 then set mm to "0" & mm
  return (h12 as text) & ":" & mm & " " & ap
end clockLabel

on splitWords(theText)
  set oldDelims to AppleScript's text item delimiters
  set AppleScript's text item delimiters to " "
  set parts to text items of theText
  set AppleScript's text item delimiters to oldDelims
  set out to {}
  repeat with p in parts
    if (p as text) is not "" then set end of out to (p as text)
  end repeat
  return out
end splitWords

on wordsFrom(theWords, startAt)
  set out to {}
  if startAt > (count of theWords) then return out
  repeat with i from startAt to (count of theWords)
    set end of out to item i of theWords
  end repeat
  return out
end wordsFrom

on isDigits(theText)
  if theText is "" then return false
  repeat with ch in characters of theText
    if (offset of (ch as text) in "0123456789") is 0 then return false
  end repeat
  return true
end isDigits

on trimText(theText)
  set ws to {" ", tab, return, linefeed}
  repeat while theText is not "" and ((first character of theText) is in ws)
    set theText to my dropFirstChar(theText)
  end repeat
  repeat while theText is not "" and ((last character of theText) is in ws)
    set theText to my dropLastChar(theText)
  end repeat
  return theText
end trimText

on cleanText(theText)
  set theText to my trimText(theText)
  repeat while theText is not "" and (last character of theText) is "."
    set theText to my dropLastChar(theText)
  end repeat
  return my trimText(theText)
end cleanText

-- Slicing a one-character string with "thru -2" or "thru -1" is an error in
-- AppleScript, so the one-character case is handled explicitly.
on dropFirstChar(theText)
  if (count of theText) is 1 then return ""
  return text 2 thru -1 of theText
end dropFirstChar

on dropLastChar(theText)
  if (count of theText) is 1 then return ""
  return text 1 thru -2 of theText
end dropLastChar
