-- web_ready.applescript: "make these images web ready" (skill images.web_ready).
--
-- For each image: scale so the longest side is at most 1600 px (never upscaled),
-- then write a JPEG (quality 0.8) beside the original as "<name>-web.jpg", or
-- "<name>-web 2.jpg", "<name>-web 3.jpg" ... when that name is taken. Never
-- overwrites. The picture is drawn into a fresh bitmap, so no EXIF, GPS or XMP
-- from the source is carried into the output. Originals are read only: never
-- modified, moved or deleted.
--
-- argv:
--   ["selection"]               the manifest's mode: the Finder selection. Every
--                               image in it must sit inside Downloads, Documents,
--                               Desktop or Pictures.
--   ["files", <POSIX path>...]  TEST ONLY. The manifest never uses it. It skips
--                               the folder check so tests can use a temp folder.
--
-- Output: "made N web-ready images", plus " (skipped M)" when some failed.
-- Exits with an error when nothing is selected, when there are more than 15
-- images, or when no image could be made.

use AppleScript version "2.4"
use scripting additions
use framework "Foundation"
use framework "AppKit"

property maxSide : 1600
property maxImages : 15 -- about 0.6 s per large HEIC; keeps a batch inside the 15 s step limit
property jpegQuality : 0.8
property imageExtensions : {"jpg", "jpeg", "png", "heic", "heif", "tiff", "gif", "webp", "bmp"}
property allowedFolders : {"Downloads", "Documents", "Desktop", "Pictures"}

on run argv
  if (count of argv) is 0 then error "usage: selection | files <paths>"
  set mode to item 1 of argv
  if mode is "selection" then
    set candidates to my finderSelection()
    set testMode to false
  else if mode is "files" then
    set candidates to items 2 thru -1 of argv
    set testMode to true
  else
    error "unknown mode " & mode
  end if
  set images to my pickImages(candidates, testMode)
  if (count of images) is 0 then error "select some images in Finder"
  if (count of images) > maxImages then error "select at most 15 images at a time"

  set madeCount to 0
  set skippedCount to 0
  repeat with imagePath in images
    try
      my makeWebReady(imagePath as text)
      set madeCount to madeCount + 1
    on error
      set skippedCount to skippedCount + 1
    end try
  end repeat
  if madeCount is 0 then error "could not make any of the " & skippedCount & " images web-ready"

  set summary to "made " & madeCount & " web-ready images"
  if skippedCount > 0 then set summary to summary & " (skipped " & skippedCount & ")"
  return summary
end run

-- POSIX paths of the Finder selection, folders left out.
on finderSelection()
  set selected to {}
  try
    tell application "Finder"
      set selected to selection as alias list
    end tell
  on error
    return {}
  end try
  set picked to {}
  repeat with entry in selected
    set p to POSIX path of entry
    if p does not end with "/" then set end of picked to p
  end repeat
  return picked
end finderSelection

-- Keep the image files. In selection mode an image outside the allowed
-- folders is an error, not a silent skip, so the user learns why.
on pickImages(candidates, testMode)
  set homeDir to POSIX path of (path to home folder)
  set roots to {}
  repeat with folderName in allowedFolders
    set end of roots to homeDir & folderName & "/"
  end repeat
  set picked to {}
  repeat with candidate in candidates
    set p to candidate as text
    if my isImagePath(p) then
      if (not testMode) and (my insideRoots(p, roots) is false) then
        error "pick images inside Downloads, Documents, Desktop or Pictures"
      end if
      set end of picked to p
    end if
  end repeat
  return picked
end pickImages

on insideRoots(p, roots)
  if p contains "/../" then return false
  repeat with r in roots
    if p starts with (r as text) then return true
  end repeat
  return false
end insideRoots

on isImagePath(p)
  set ext to my extensionOf(p)
  return imageExtensions contains ext
end isImagePath

on extensionOf(p)
  set nsPath to current application's NSString's stringWithString:p
  return (nsPath's pathExtension()) as text
end extensionOf

-- Decode, scale and re-encode one image into a new JPEG beside it.
on makeWebReady(srcPath)
  set img to current application's NSImage's alloc()'s initWithContentsOfFile:srcPath
  if img is missing value then error "cannot read " & srcPath
  set reps to img's representations()
  set rep to reps's firstObject()
  set pw to (rep's pixelsWide()) as integer
  set ph to (rep's pixelsHigh()) as integer

  set longest to pw
  if ph > longest then set longest to ph
  set scaleBy to 1
  if longest > maxSide then set scaleBy to maxSide / longest
  set outW to round (pw * scaleBy) rounding as taught in school
  set outH to round (ph * scaleBy) rounding as taught in school
  if outW < 1 then set outW to 1
  if outH < 1 then set outH to 1

  set noPlanes to missing value
  set outRep to current application's NSBitmapImageRep's alloc()'s initWithBitmapDataPlanes:noPlanes pixelsWide:outW pixelsHigh:outH bitsPerSample:8 samplesPerPixel:4 hasAlpha:true isPlanar:false colorSpaceName:(current application's NSCalibratedRGBColorSpace) bytesPerRow:0 bitsPerPixel:32
  set ctx to current application's NSGraphicsContext's graphicsContextWithBitmapImageRep:outRep
  current application's NSGraphicsContext's setCurrentContext:ctx
  ctx's setImageInterpolation:(current application's NSImageInterpolationHigh)
  -- white under the picture: transparent PNG/HEIC areas become white, not black
  set white to current application's NSColor's whiteColor()
  white's setFill()
  set canvas to current application's NSBezierPath's bezierPathWithRect:{{0, 0}, {outW, outH}}
  canvas's fill()
  img's drawInRect:{{0, 0}, {outW, outH}}
  current application's NSGraphicsContext's setCurrentContext:(missing value)

  set quality to current application's NSNumber's numberWithDouble:jpegQuality
  set props to current application's NSMutableDictionary's dictionary()
  props's setObject:quality forKey:(current application's NSImageCompressionFactor)
  set jpgData to outRep's representationUsingType:(current application's NSJPEGFileType) |properties|:props
  if jpgData is missing value then error "could not encode " & srcPath

  set outPath to my freeWebPath(srcPath)
  set written to jpgData's writeToFile:outPath atomically:true
  if written is not true then error "could not write " & outPath
  return outPath
end makeWebReady

-- "<stem>-web.jpg", or "<stem>-web 2.jpg", ... the first name not yet taken.
on freeWebPath(srcPath)
  set nsPath to current application's NSString's stringWithString:srcPath
  set stem to (nsPath's stringByDeletingPathExtension()) as text
  set fm to current application's NSFileManager's defaultManager()
  set candidate to stem & "-web.jpg"
  set n to 2
  repeat while (fm's fileExistsAtPath:candidate)
    set candidate to stem & "-web " & n & ".jpg"
    set n to n + 1
  end repeat
  return candidate
end freeWebPath
