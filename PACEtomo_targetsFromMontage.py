#!Python
# ===================================================================
#ScriptName     PACEtomo_targetsFromMontage
# Author:       Fabian Eisenstein
# Created:      2022/12/09
# Revision:     v1.18
# Last Change:  2026/10/16: added markGroupFirstPoints so that many groups can be prepared in ONE Acquire at Items pass:
#               it marks the first point of every Navigator group for acquisition (and clears the other points of those
#               groups) and then stops, and in a batch run (noUI) the script moves the Acquire flag from that point on
#               to the map of target 1, which is what the next Acquire at Items pass runs the acquisition at.
#               2026/10/16: in the liveReferences mode the Navigator map entries are now made by SERIALEM too, with
#               NewMap called while the reference file is still open (so the entry is built by SerialEM with its own
#               keys and points at the file it wrote), followed by ChangeItemLabel, ChangeItemNote and SetItemAcquire.
#               The hand-written Navigator file machinery (parsing the file, building entries from the templates,
#               rewriting and merging) is then not used at all and the file is only saved, so no unreadable item
#               anywhere in the Navigator can keep the reference maps out of it. The crop mode still uses the old
#               machinery, and both fall back with a warning if NewMap cannot be used (images outside buffer A).
#               2026/10/16: in the liveReferences mode the View and Preview images are now saved BY SERIALEM (OpenNewFile
#               with the exact name, which bypasses the file properties dialog, then Save and CloseFile) instead of being
#               read out of the buffer and written with mrcfile. The reference files are then exactly like images taken by
#               the camera: SerialEM's own MRC header with the camera values, pixel size, exposure, intensity and its
#               extended header. The image that the script keeps in memory is still used for the map entries and as a
#               fallback if the save fails, and the best aligned image is only saved by SerialEM when it is the one still
#               in the buffer; otherwise it falls back to writing that image directly.
#               2026/10/16: the cropped reference images no longer look dim. SerialEM loads maps converted to bytes for
#               display ("Convert Maps to Bytes", on by default), so the image the script reads from the buffer has a
#               range of 0..255 while the file holds the values written by the camera (0..12576 in the session that was
#               checked), and a reference made from it looked dim. The crops are now scaled onto the value range stored
#               in the map file, so they have the same values and clip like an image taken by the camera. The
#               liveReferences mode does not need this, since it takes live images. The version reported in the log is
#               now taken from one place (scriptVersion) instead of a string that had gone stale.
#               2026/10/16: fixed the reason why the Navigator items of this script could not be read back: MapBinning
#               was written as "2.0" where SerialEM requires an integer (the binning comes back as a float from
#               ImageProperties and is multiplied by the binFactor for Gatan cameras), so the reader reported every
#               item of the run as "gave an error getting the data" and the reference maps could not be used. Both the
#               value itself and the writer (NavValue) now emit whole numbers without a decimal point, which also
#               repairs Navigator files written by earlier versions.
#               2026/10/15: the new Navigator items are now added by writing them to a separate file and merging it
#               (MergeNavFile), instead of rewriting the whole Navigator file and reading it back in. A read of the
#               whole file fails when ANY of its items refers to a map file that cannot be found or opened, which kept
#               the reference maps of the run out of the Navigator; the merge reads only the new items. If the merge
#               fails, the files written by the script are read back once with OpenOldFile + ReadFile to tell whether
#               they are the problem. WriteNav no longer consumes the item labels, so the same items can be written to
#               the merge file and, if needed, to the whole file in the fallback.
#               2026/10/15: every message is now also written to PACEtomo_targetsFromMontage.log in the current
#               directory (the log window cannot be copied on the microscope), the log starts with the path and the
#               modification time of the file that is actually running and with the mode (liveReferences or cropping),
#               because the script is edited here but run from a copy on the microscope PC, and each point now says
#               whether it takes live images or crops.
#               2026/10/15: added the liveReferences mode: a real View and Preview image are taken at every point and
#               used as its references, which removes the resampling and the padding of the virtual maps and gives the
#               references the resolution of the live images. Before each pair of reference images the target is
#               re-aligned to the position of the point in the map with the image shift (FindOffsetInImage locates the
#               map content around the point in the live View image; OffsetToStage converts the offset with SerialEM's
#               image to stage transformation and ImageShiftByStageDiff applies it), because the position of a point
#               taken from the map is not accurate enough and the target has to sit in the centre of its references -
#               that is what PACEtomo aligns the acquired images to. The correction inverts itself if it does not
#               improve the position, and the offsets before/after are logged.
#               2026/10/15: points of a group are now cropped from the map they were actually added on. SerialEM
#               reports the image coordinates of a group only for the map that is loaded, so a group whose points
#               were added on several maps (e.g. on sections 2,3,4 of a multi-section view map) could not be
#               cropped: the previous version either aborted, or (before the abort was added) dropped those points
#               and cropped the following targets at the wrong place. The maps each point was added on are now
#               taken from the Navigator file, each map is loaded when needed and the coordinates are queried per
#               item (ReportItemImageCoords); the result is validated against the group coordinates, so a
#               disagreement falls back to the old behaviour. Also added the version and progress lines in the log.
#               2026/10/15: the targets file is now written BEFORE the Navigator is updated, and updating the Navigator
#               is no longer fatal: SerialEM refuses to read a Navigator file when any of its items refers to a map file
#               that cannot be found or opened (reported as "N items could not be read in from Navigator file ... gave an
#               error getting the data"), which used to abort the script after the maps had already been made and left no
#               targets file; added a check that all points of the group are on the map that gets loaded, since
#               GetNavGroupImageCoords only returns the points inside that image (points added on other maps, e.g. on
#               sections 2/3/4 of a multi-section View map, were silently dropped and mis-placed the crops or crashed
#               with an IndexError)
#               2026/09/18: virtual maps are cropped to EXACTLY the dimensions and pixel size of the real low-dose View and
#               Record template images (previously the template pixel size was divided by the binning again and the crop was
#               sized from the camera properties, so the maps had a wrong scale and did not match live images; e.g. K3 bin 8
#               gave 720x511 at the wrong pixel size vs 720x508 live, which made SerialEM's alignment/map matching fail or stall)
#               2026/01/31: added onlyView mode to skip generation of Preview maps
#               2024/05/14: added warning for items not on map, added proper check for camera type

# Take a montage of your target area. 
# Select targets using the navigator with "Add Points" (points have to be part of the same group).
# Drag the tracking target position to the top of the group.
# Select one of the points and run the script.
# (Make sure drawing of points is not disabled in Navigator.)
# You can run the script in the DUMMY version. Just ignore the error messages. It does need a "template" map for Record and View mode in that case. These should have the Navigator Notes "Template Preview" and "Template View", respectively.
# You can also run the script using Acquire at Items. Just check "Skip stage move to item if possible" in the Task-related options (Acquire at Items window).

##### SETTINGS #####

onlyView = False    # set to True to only create View maps and not Preview maps and targets file
markGroupFirstPoints = False   # set to True to only mark the first point of every Navigator group for acquisition
                    # (and clear the other points of those groups) and then stop, so that ONE Acquire at Items pass
                    # with this script as the per-item script prepares all groups at once
noUI    = False     # set to True to avoid folder/name selection (e.g. to run the script in a Acquire at Items routine), it will use the label of the montage as name template
prefix  = "pos"     # prefix for name when running noUI
liveReferences = True      # True: take a real View and Preview image at every point and use them as the references of
                    # that target (like PACEtomo_selectTargets), re-aligning to the point in the map first. The
                    # references then contain the resolution of the live images instead of the map, at the cost of one
                    # View and one Preview exposure per point. False: crop the references out of the montage map.
scriptVersion = "v1.18 (2026/10/16)"    # reported at the start of a run, so the file that actually ran is identifiable
refAlignTol = 0.05  # target position tolerance [microns] of the re-alignment in the liveReferences mode; the target is
                    # re-aligned with the image shift until it is centred within this distance

### END SETTINGS ###

import serialem as sem
import os
import time
import copy
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.image as mpimg
import mrcfile
from skimage.transform import resize

### FUNCTIONS ###

def WriteTargets(targetFile, targets, savedRun=False, resume={"sec": 0, "pos": 0}, settings={}):
    output = ""
    if settings != {}:
        for key, val in settings.items():
            if val != "":
                output += "_set " + key + " = " + str(val) + "\n"
        output += "\n"
    if resume["sec"] > 0 or resume["pos"] > 0:
        output += "_spos = " + str(resume["sec"]) + "," + str(resume["pos"]) + "\n" * 2
    for pos in range(len(targets)):
        output += "_tgt = " + str(pos + 1).zfill(3) + "\n"
        for key in targets[pos].keys():
            output += key + " = " + str(targets[pos][key]) + "\n"
        if savedRun:
            output += "_pbr" + "\n"
            for key in savedRun[pos][0].keys():
                output += key + " = " + str(savedRun[pos][0][key]) + "\n"
            output += "_nbr" + "\n"
            for key in savedRun[pos][1].keys():
                output += key + " = " + str(savedRun[pos][1][key]) + "\n"
        output += "\n"
    with open(targetFile, "w") as f:
        f.write(output)

def WriteMrc(outfilename, image, pixSize):
    with mrcfile.new(os.path.join(curDir, outfilename), overwrite=True) as mrc:
        mrc.set_data(image)
        mrc.voxel_size = (pixSize, pixSize, pixSize)
        mrc.update_header_from_data()
        mrc.update_header_stats()

def ParseNav(navFile):
    with open(navFile) as f:
        navContent = f.readlines()
    header = []
    items = []
    newItem = {}
    index = 0

    for line in navContent:
        col = line.rstrip().split(" ")
        if col[0] == "": 
            if "Item" in newItem.keys():
                items.append(newItem)
                newItem = {}
                continue
            else:
                continue
        if line.startswith("[Item"):
            index += 1
            newItem = {"index": index, "Item": col[2].strip("]")}
        elif "Item" in newItem.keys():
            newItem[col[0]] = [val for val in col[2:]]
        else:
            header.append(line)
    if "Item" in newItem.keys():    #append last target
        items.append(newItem)
    return header, items

def MapFilePath(navItems, mapIndex):
    # Path of the MRC file of the map at the given table index, or None. The Navigator file separates values by
    # spaces, so a path containing spaces arrives as several words and has to be joined again.
    if not (0 < mapIndex <= len(navItems)):
        return None
    item = navItems[mapIndex - 1]
    if "MapFile" not in item or len(item["MapFile"]) == 0:
        return None
    return " ".join(item["MapFile"])

def ReadMrcRange(filename):
    # The value range (dmin, dmax) stored in an MRC file, or None. Reads only the header.
    if filename is None:
        return None
    try:
        with mrcfile.open(filename, header_only=True, permissive=True) as mrc:
            low, high = float(mrc.header.dmin), float(mrc.header.dmax)
        if high > low:
            return low, high
    except Exception:
        pass
    return None

def NavValue(val):
    # Values of some keys have to be integers (e.g. MapBinning, MapMagInd, MapSection): SerialEM reports the whole item
    # as unreadable ("items could not be read in from Navigator file ... gave an error getting the data") when such a
    # key holds "2.0" instead of "2", and then every item of that run is lost. A number that is a whole number is
    # therefore always written without a decimal point, whether it is a number or a string read from a file, which also
    # repairs files written by earlier versions of this script.
    if isinstance(val, (float, np.floating)) and float(val) == int(val):
        return str(int(val))
    if isinstance(val, str) and "." in val:
        try:
            if float(val) == int(float(val)):
                return str(int(float(val)))
        except ValueError:
            pass
    return str(val)

def WriteNav(header, items, filename):
    # the items are written to a separate Navigator file, either to be merged into the Navigator or to be read back in
    # as the whole file, so the labels and indices of the items are not consumed here
    text = ""
    for line in header:
        text += line
    text += os.linesep
    for navItem in items:
        item = dict(navItem)
        text += "[Item = " + item["Item"] + "]" + os.linesep
        item.pop("Item")
        item.pop("index")
        for key, attr in item.items():
            text += key + " = "
            for val in attr:
                text += NavValue(val) + " "
            text += os.linesep
        text += os.linesep
    with open(filename, "w", newline="") as f:
        f.write(text)
    return

def CropImage(image, coords, fov):
    imageCrop = image[max(0, int(coords[0] - fov[0] / 2)):min(image.shape[0], int(coords[0] + fov[0] / 2)), max(0, int(coords[1] - fov[1] / 2)):min(image.shape[1], int(coords[1] + fov[1] / 2))]
    if imageCrop.shape != fov:
        mean = np.mean(imageCrop)
        if imageCrop.shape[0] < fov[0]:
            padding = np.full((int(fov[0] - imageCrop.shape[0]), imageCrop.shape[1]), mean)
            if int(coords[0] - fov[0] / 2) < 0:
                imageCrop = np.concatenate((padding, imageCrop), axis=0)
            if int(coords[0] + fov[0] / 2) > image.shape[0]:
                imageCrop = np.concatenate((imageCrop, padding), axis=0)
        if imageCrop.shape[1] < fov[1]:
            padding = np.full((imageCrop.shape[0], int(fov[1] - imageCrop.shape[1])), mean)
            if int(coords[1] - fov[1] / 2) < 0:
                imageCrop = np.concatenate((padding, imageCrop), axis=1)
            if int(coords[1] + fov[1] / 2) > image.shape[1]:
                imageCrop = np.concatenate((imageCrop, padding), axis=1)
        WriteLog("WARNING: Target position is close to the edge of the map and was padded.")
    return imageCrop

def FovInSourcePixels(sizeX, sizeY, pixSize, srcPixSize):
    # area covered by an image of sizeX x sizeY at pixel size pixSize, in pixels of an image with pixel size srcPixSize
    return int(sizeY * pixSize / srcPixSize), int(sizeX * pixSize / srcPixSize)

def CropFits(image, coords, fov):
    # whether a crop of size fov (y, x) centred on coords (y, x) lies completely inside the image
    return (coords[0] - fov[0] / 2 >= 0 and coords[0] + fov[0] / 2 <= image.shape[0] and
            coords[1] - fov[1] / 2 >= 0 and coords[1] + fov[1] / 2 <= image.shape[1])

def CropWithMask(image, coords, fov):
    # crop of size fov (y, x) centred on coords (y, x) without padding, and a mask of the pixels covered by the image
    y0, x0 = int(coords[0] - fov[0] / 2), int(coords[1] - fov[1] / 2)
    crop = np.zeros(fov, dtype=float)
    mask = np.zeros(fov, dtype=bool)
    yStart, yEnd = max(0, y0), min(image.shape[0], y0 + fov[0])
    xStart, xEnd = max(0, x0), min(image.shape[1], x0 + fov[1])
    if yEnd > yStart and xEnd > xStart:
        crop[yStart - y0:yEnd - y0, xStart - x0:xEnd - x0] = image[yStart:yEnd, xStart:xEnd]
        mask[yStart - y0:yEnd - y0, xStart - x0:xEnd - x0] = True
    return crop, mask

def CorrelationAtShift(a, b, shift):
    # normalised correlation coefficient of a and b when b is shifted by shift, used as quality measure of a match
    shifted = np.roll(np.roll(b, int(round(shift[0])), axis=0), int(round(shift[1])), axis=1)
    norm = np.sqrt(np.sum(a * a) * np.sum(shifted * shifted))
    return float(np.sum(a * shifted) / norm) if norm > 0 else 0.0

def FindOffsetInImage(mapImage, mapCoords, mapPixSize, image, pixSize):
    # Locate the content at mapCoords of mapImage in image by cross-correlation and return its offset (dy, dx) from the
    # centre of image in pixels of image. Also returns the match quality (normalised correlation coefficient at that
    # offset) and how much the best peak stands out from the next one. Returns dy = dx = None if too little of the new
    # image is covered by the map or the match is not reliable. Both images must have the same orientation (same camera
    # and the same rotation of the magnification).
    fov = FovInSourcePixels(image.shape[1], image.shape[0], pixSize, mapPixSize)
    patch, mask = CropWithMask(mapImage, mapCoords, fov)
    if mask.sum() < 0.25 * mask.size:
        return None, None, 0.0, 0.0
    patch = np.where(mask, patch, patch[mask].mean())
    patch = resize(patch, image.shape, preserve_range=True, anti_aliasing=True)
    maskImage = resize(mask.astype(float), image.shape, order=0, preserve_range=True) > 0.5
    if maskImage.sum() < 0.25 * maskImage.size:
        return None, None, 0.0, 0.0
    a = patch - patch[maskImage].mean()
    b = image - image[maskImage].mean()
    a[~maskImage] = 0.0
    b[~maskImage] = 0.0
    corr = np.fft.ifft2(np.fft.fft2(a) * np.conj(np.fft.fft2(b))).real
    peak = np.array(np.unravel_index(np.argmax(corr), corr.shape), dtype=float)
    shift = np.where(peak > np.array(corr.shape) / 2, peak - np.array(corr.shape), peak)
    quality = CorrelationAtShift(a, b, shift)
    # The best peak has to stand out from the next one, because a periodic specimen (holes, grid bars) gives several
    # similar peaks and the offset would be ambiguous. The offset also has to be small enough for the target to be
    # inside the image at all.
    nextPeaks = corr.copy()
    row, col = int(peak[0]) % corr.shape[0], int(peak[1]) % corr.shape[1]
    nextPeaks[max(0, row - 5):row + 6, max(0, col - 5):col + 6] = -np.inf
    nextPeak = float(nextPeaks.max())
    sharpness = float(corr.max() / nextPeak) if nextPeak > 0 else float("inf")
    dy, dx = -shift[0], -shift[1]                         # offset of the content from the centre of image
    if (quality < 0.25 or sharpness < 2.0 or abs(dy) > 0.45 * image.shape[0] or abs(dx) > 0.45 * image.shape[1]):
        return None, None, quality, sharpness
    return dy, dx, quality, sharpness

def GroupLeaderPoints(navItems):
    # The first point of every Navigator group, in table order, from a parsed Navigator file, together with the table
    # indices of the items belonging to each group. A group whose first item is not a point is left out, because the
    # script has to be run on a point (the tracking point is kept at the top of its group).
    # Returns (leaders, members), leaders = [(group ID, table index, label, stage X, stage Y), ...],
    # members = {group ID: [table indices]}.
    leaders, members, firstType = [], {}, {}
    for index, item in enumerate(navItems, start=1):
        if "GroupID" not in item:
            continue
        groupID = int(item["GroupID"][0])
        if groupID == 0:
            continue                      # 0 means: not in a group
        members.setdefault(groupID, []).append(index)
        if groupID in firstType:
            continue
        firstType[groupID] = item.get("Type", ["-1"])[0]
        if firstType[groupID] == "0":
            stage = item.get("StageXYZ", ["0", "0", "0"])
            leaders.append((groupID, index, item.get("Item", ""), stage[0], stage[1]))
    return leaders, members

def GroupItemsFromNav(navItems, groupID, stageX, stageY):
    # Points of a group and the map each of them was added on, from a parsed Navigator file, in table order.
    # Returns (points, maps) with points = [(table index, item), ...] and maps = {map ID: table index}; or
    # (None, None) if the points in the file do not agree with the stage coordinates SerialEM reports for the group
    # (then the file cannot be used to tell which point belongs to which map).
    points = []
    for index, item in enumerate(navItems, start=1):
        if "GroupID" not in item or int(item["GroupID"][0]) != groupID:
            continue
        if "MapID" in item or "DrawnID" not in item or "StageXYZ" not in item:
            continue                       # maps and items without a drawn-on map are not targets
        points.append((index, item))
    if len(points) != stageX.size or len(points) != stageY.size:
        return None, None
    for (index, item), x, y in zip(points, stageX, stageY):
        if abs(float(item["StageXYZ"][0]) - x) > 0.02 or abs(float(item["StageXYZ"][1]) - y) > 0.02:
            return None, None              # the order or the positions do not agree
    maps = {}
    for index, item in enumerate(navItems, start=1):
        if "MapID" in item:
            maps[int(item["MapID"][0])] = index
    return points, maps

def PointImageCoords(itemIndex, buf):
    # Pixel coordinates (y, x) of a Navigator item on the image in the buffer, or None if they cannot be obtained.
    # This works for any map, so a point can be placed on the map it was added on even if another map is loaded.
    try:
        coords = sem.ReportItemImageCoords(itemIndex, buf)
    except Exception:
        return None
    if not isinstance(coords, (list, tuple)) or len(coords) < 2:
        return None
    try:
        return float(coords[1]), float(coords[0])       # the command reports X and Y; images are indexed (y, x)
    except (TypeError, ValueError):
        return None

def SaveAndMapReference(path, buf, label, note, acquire=False):
    # Save the image in the buffer with SerialEM and turn it into a labelled Navigator map in one go, so that the file
    # AND the map entry are made by SerialEM itself (with its own keys: MapBinning, MapScaleMat, MapWidthHeight,
    # MapMinMaxScale, the magnification and so on, and pointing at the file that was just written). The file
    # properties dialog is bypassed by OpenNewFile; NewMap has to be called while the file is still open, because the
    # image has to be "saved already, or savable in the current file", and it maps the image in buffer A. Returns
    # (filename that was written, index of the new item) with either of them None if that part failed.
    if buf != "A":
        return None, None                 # NewMap always uses buffer A: the crops are written by the script instead
    written, itemIndex = None, None
    try:
        sem.OpenNewFile(path)
        sem.S(buf)
        written = sem.ReportCurrentFilename()
        itemIndex = sem.NewMap()
        sem.CloseFile()
    except Exception as e:
        WriteLog("WARNING: Saving " + os.path.basename(path) + " and making its Navigator map failed (" + str(e) + ").")
        try:
            sem.CloseFile()
        except Exception:
            pass
        return written, None
    if isinstance(written, (list, tuple)):
        written = written[0] if len(written) > 0 else None
    if isinstance(itemIndex, (list, tuple)):
        itemIndex = itemIndex[0] if len(itemIndex) > 0 else None
    if isinstance(written, str) and len(written) > 0 and os.path.abspath(written) != os.path.abspath(path):
        WriteLog("NOTE: SerialEM wrote " + os.path.basename(written) + " instead of " + os.path.basename(path) + "; the targets file uses that name.")
    if itemIndex is not None:
        try:
            sem.ChangeItemLabel(int(itemIndex), label)
            sem.ChangeItemNote(int(itemIndex), note)
            if acquire:
                sem.SetItemAcquire(int(itemIndex), 1)
        except Exception as e:
            WriteLog("WARNING: The label, note or Acquire flag of the new map (item " + str(itemIndex) + ") could not be set (" + str(e) + "); set them by hand: label " + label + ", note " + note + ".")
    return written, itemIndex

def SaveBufferWithSerialEM(path, buf):
    # Save the image in the buffer with SerialEM itself and return the name of the file that was written, or None if
    # it failed. The file is then exactly like an image taken by the camera: SerialEM's own MRC header with the camera
    # values, the pixel size, the exposure and intensity, and its extended header. OpenNewFile uses the name as given
    # and bypasses the file properties dialog; if SerialEM modified the name, the file (and its metadata file) is
    # renamed to the intended name, which is what the targets file and the Navigator entries refer to.
    try:
        sem.OpenNewFile(path)
        sem.S(buf)
        written = sem.ReportCurrentFilename()
        sem.CloseFile()
    except Exception as e:
        WriteLog("WARNING: Saving " + os.path.basename(path) + " with SerialEM failed (" + str(e) + "); writing it directly instead.")
        try:
            sem.CloseFile()
        except Exception:
            pass
        return None
    if isinstance(written, (list, tuple)):
        written = written[0] if len(written) > 0 else ""
    if not isinstance(written, str) or len(written) == 0:
        return path
    if os.path.abspath(written) == os.path.abspath(path):
        return path
    try:
        os.replace(written, path)
        if os.path.exists(written + ".mdoc"):
            os.replace(written + ".mdoc", path + ".mdoc")
        WriteLog("NOTE: SerialEM wrote " + os.path.basename(written) + "; renamed it to " + os.path.basename(path) + ".")
        return path
    except Exception as e:
        WriteLog("WARNING: The file " + written + " could not be renamed to " + path + " (" + str(e) + "); the targets file and the Navigator entries expect the name " + path + ".")
        return written

def OffsetToStage(dy, dx, buf):
    # Stage shift [microns] that moves the image content by (dy, dx) pixels, using SerialEM's image to stage
    # transformation for the image in the buffer (image coordinates have Y from the top, as in SerialEM's commands).
    try:
        matrix = sem.BufImageToStageMatrix(buf, 1)
    except Exception:
        return None, None
    if not isinstance(matrix, (list, tuple)) or len(matrix) < 4:
        return None, None
    try:
        xPerX, xPerY, yPerX, yPerY = [float(value) for value in matrix[:4]]
        return dx * xPerX + dy * xPerY, dx * yPerX + dy * yPerY
    except (TypeError, ValueError):
        return None, None

### END FUNCTIONS ###

### LOG ###

# Every message of this script is echoed to the SerialEM log window and written to a log file, so that a failure can
# be reported without copying the log window. The file is created in the current directory of SerialEM (change it with
# UserSetDirectory at the start of a run if you want it somewhere else).

logPath = None
logBuffer = []
try:
    logPath = os.path.join(sem.ReportDirectory(), "PACEtomo_targetsFromMontage.log")
    open(logPath, "w").close()
except Exception:
    logPath = None

def WriteLog(text):
    sem.Echo(text)
    if logPath is None:
        logBuffer.append(text)
        return
    try:
        with open(logPath, "a") as logHandle:
            logHandle.write(text + os.linesep)
    except Exception:
        pass

### SETTINGS DONE ###

s2ssMatrix = None
dummy = False
try:
    WriteLog("NOTE: Script file: " + os.path.abspath(__file__) + " (last modified " + time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(__file__))) + ")")
except Exception:
    WriteLog("NOTE: Script file: unknown (the script was pasted into the script window or __file__ is not available).")
if logPath is None:
    WriteLog("WARNING: No log file could be created in the current directory; the messages below are only in the log window.")
else:
    WriteLog("NOTE: Writing a log of this run to " + logPath)

if sem.ReportProperty("DummyInstance") == 1:
    if not onlyView:
        WriteLog("WARNING: You are using SerialEM in DUMMY mode. Make sure a View image and Preview image template are available in the Navigator!")
    else:
        WriteLog("WARNING: You are using SerialEM in DUMMY mode. Make sure a View image template is available in the Navigator!")
    dummy = True

if onlyView:
    WriteLog("NOTE: Running in View-only mode: Only View maps will be created. Please change the setting in the script if you want to create Preview maps as well.")

WriteLog("NOTE: PACEtomo_targetsFromMontage, Revision " + scriptVersion + ". If this line does not show the version you expect, the script that ran is not this file.")

if markGroupFirstPoints:
    # Mark the first point of every group for acquisition, so that a single Acquire at Items pass can prepare all
    # groups: the script then runs once per group and moves the Acquire flag on to the map of target 1 (see below).

    WriteLog("NOTE: markGroupFirstPoints is on: marking the first point of every group for acquisition.")
    try:
        markNavFile = sem.ReportNavFile()
    except Exception as e:
        WriteLog("ERROR: There is no Navigator file defined (" + str(e) + "); save the Navigator first (Navigator - Save).")
        sem.Exit()
    markHeader, markItems = ParseNav(markNavFile)
    markLeaders, markMembers = GroupLeaderPoints(markItems)
    if len(markLeaders) == 0:
        WriteLog("WARNING: No groups were found in " + markNavFile + ".")
    for groupID, index, label, stageX, stageY in markLeaders:
        try:
            sem.SetItemAcquire(index, 1)
            WriteLog("NOTE: Group " + str(groupID) + ": point " + str(label) + " at stage " + str(stageX) + ", " + str(stageY) + " is marked for acquisition.")
        except Exception as e:
            WriteLog("WARNING: The point " + str(label) + " of group " + str(groupID) + " could not be marked (" + str(e) + ").")
        for index in markMembers[groupID]:
            if index != markMembers[groupID][0]:
                try:
                    sem.SetItemAcquire(index, 0)
                except Exception:
                    pass
    WriteLog("NOTE: " + str(len(markLeaders)) + " group(s) marked; the other points of those groups are cleared.")
    WriteLog("NOTE: Now run Acquire at Items with this script as the per-item script and 'Skip stage move to item if possible' checked.")
    try:
        sem.SaveNavigator()
        WriteLog("NOTE: The Navigator file was saved.")
    except Exception as e:
        WriteLog("WARNING: Saving the Navigator file failed: " + str(e) + "; save it by hand.")
    sem.Exit()

# Get nav item info

navID = int(sem.ReportNavItem()[0])
groupInfo = sem.ReportGroupStatus()
groupID = int(groupInfo[1])
drawnID = int(sem.NavIndexItemDrawnOn(navID))

if drawnID == 0:
    if sem.ReportIfNavAcquiring()[0]:
        sem.SkipAcquiringNavItem()
        sem.EndAcquireAtItems()     # only available from 12-May-23
        WriteLog("NOTE: This nav item is not a point on a map and will be ignored.")
        sem.Exit()
    else:
        WriteLog("ERROR: This nav item is not a point on a map. Make sure to save an image as a map prior to adding points!")
        sem.Exit()

# Get user input

if not noUI:
    sem.UserSetDirectory("Please choose a directory for saving targets and tilt series!")

    sem.EnterString("userName","Please provide a rootname for the PACE-tomo collection area!")
    userName = sem.GetVariable("userName")
else:
    sem.ReportOtherItem(drawnID)
    navLabel = sem.GetVariable("navLabel")
    userName = prefix + navLabel.strip().replace(" ", "_")

curDir = sem.ReportDirectory()
tgtsFilePath = os.path.join(curDir, userName + "_tgts.txt")

if logPath is None:                 # no log file could be created before the user chose a directory
    try:
        logPath = os.path.join(curDir, "PACEtomo_targetsFromMontage.log")
        open(logPath, "w").close()
        for logLine in logBuffer:
            with open(logPath, "a") as logHandle:
                logHandle.write(logLine + os.linesep)
        logBuffer = []
        WriteLog("NOTE: Writing a log of this run to " + logPath)
    except Exception:
        logPath = None

# Make sure tgts file is unique
counter = 1
while os.path.exists(tgtsFilePath):
    counter += 1
    tgtsFilePath = os.path.join(curDir, userName + str(counter) + "_tgts.txt") 
if counter > 1:
    userName = userName + str(counter)
    WriteLog("NOTE: A targets file with this name already exists, using the name " + userName + "_tgts.txt for this run.")

WriteLog("NOTE: Targets file of this run: " + tgtsFilePath)

# Make sure nav contains template images

prevID = int(sem.NavIndexWithNote("Template Preview"))
viewID = int(sem.NavIndexWithNote("Template View"))
if (prevID == 0 and not onlyView) or viewID == 0:
    if not dummy:
        sem.SetColumnOrGunValve(0)
        sem.AllowFileOverwrite(1)
        if prevID == 0 and not onlyView:
            sem.OpenNewFile("template_preview.mrc")
            sem.L()
            sem.S()
            prevID = int(sem.NewMap(0, "Template Preview"))
            sem.ChangeItemLabel(prevID, "TP")
            sem.CloseFile()
            if s2ssMatrix is None:
                s2ssMatrix = np.array(sem.StageToSpecimenMatrix(0)).reshape((2, 2))
        if viewID == 0:
            sem.OpenNewFile("template_view.mrc")
            sem.V()
            sem.S()
            viewID = int(sem.NewMap(0, "Template View"))
            sem.ChangeItemLabel(viewID, "TV")
            sem.CloseFile()
        sem.SetColumnOrGunValve(1)
        sem.AllowFileOverwrite(0)
    else:
        sem.OKBox("ERROR: Template for View or Preview image was not found!")
        WriteLog("ERROR: Template for View or Preview image was not found!")
        WriteLog("If you have a View and Preview image in the Navigator that you want to use as template, please change the Navigator Note to 'Template View' and 'Template Preview', respectively.")
        sem.Exit()

# Load templates: the virtual maps are cropped to EXACTLY the dimensions and pixel size of the real
# low-dose View and Record images saved as templates, so that they are indistinguishable from a live
# acquisition for SerialEM's alignment and map matching.

sem.LoadOtherMap(viewID, "A")
imgProp = sem.ImageProperties("A")
viewPixSize = imgProp[4] * 10                                                           # pixel size as stored (do NOT divide by binning)
viewSizeX, viewSizeY = imgProp[0], imgProp[1]                                           # exact template image dimensions
viewBinning = imgProp[2]                                                                # binning of the template image

if not onlyView:
    sem.LoadOtherMap(prevID, "A")
    imgProp = sem.ImageProperties("A")
    recordPixSize = imgProp[4] * 10
    recordSizeX, recordSizeY = imgProp[0], imgProp[1]
    recordBinning = imgProp[2]

    if recordPixSize == viewPixSize:
        sem.OKBox("ERROR: Template for View or Preview show the same pixel size! Make sure the maps could be loaded properly!")
        WriteLog("ERROR: Template for View or Preview show the same pixel size! Make sure the maps could be loaded properly!")
        sem.Exit()

if sem.ReportCameraProperty(0, "K2Type") > 0:
    binFactor = 2           # gatan cameras need the extra binning factor because SR is bin 1 
else:
    binFactor = 1

# Make sure s2s matrix is defined

if not dummy and s2ssMatrix is None:
    sem.GoToLowDoseArea("R")
    s2ssMatrix = np.array(sem.StageToSpecimenMatrix(0)).reshape((2, 2))

# Load montage

sem.LoadOtherMap(drawnID, "A")

buffer, *_ = sem.ReportCurrentBuffer()

imgProp = sem.ImageProperties(buffer)
x = imgProp[0]
y = imgProp[1]
pixSize = imgProp[4] * 10

image = np.asarray(sem.bufferImage(buffer), dtype=float)

# Get point coords

sem.GetNavGroupStageCoords(groupID, "groupStageX", "groupStageY", "groupStageZ")

groupStageX = np.array(sem.GetVariable("groupStageX").split(), dtype=float)
groupStageY = np.array(sem.GetVariable("groupStageY").split(), dtype=float)
stageZ = np.array(sem.GetVariable("groupStageZ").split(), dtype=float)[0]

# SerialEM places the group's points in the loaded map image and returns only the ones that lie within it, so a group
# whose points were added on more than one map (e.g. on several sections of a multi-section view map) comes back short.

try:
    sem.GetNavGroupImageCoords(groupID, buffer, "groupImageX", "groupImageY")
    groupImageX = np.array(sem.GetVariable("groupImageX").split(), dtype=float)
    groupImageY = np.array(sem.GetVariable("groupImageY").split(), dtype=float)
except Exception:
    groupImageX = np.array([])
    groupImageY = np.array([])

if groupImageX.size != groupStageX.size or groupImageY.size != groupStageY.size:
    WriteLog("ERROR: Only " + str(groupImageX.size) + " of the " + str(groupStageX.size) + " points of this group could be placed on the map that was loaded (Navigator item " + str(drawnID) + ").")
    WriteLog("ERROR: All points of a group have to be added on the same map, i.e. on the same section of the same file. Points added on other maps or on other sections of a multi-section image cannot be cropped.")
    WriteLog("ERROR: Please add the targets of this group on one map, or split them into one group per map.")
    if sem.ReportIfNavAcquiring()[0]:
        sem.SkipAcquiringNavItem()
        sem.EndAcquireAtItems()
    sem.Exit()

# Load nav file

# Needed already here: the Navigator file says which map each point of the group was added on, and points added on
# different maps cannot be cropped from one single map.

sem.SaveNavigator()
try:
    navFile = sem.ReportNavFile()
except Exception as e:
    WriteLog("ERROR: There is no Navigator file defined (" + str(e) + ").")
    WriteLog("ERROR: Save the Navigator with Navigator - Save as / Save before running this script; without a file the points and their maps cannot be read.")
    if sem.ReportIfNavAcquiring()[0]:
        sem.SkipAcquiringNavItem()
        sem.EndAcquireAtItems()
    sem.Exit()
navHeader, navItems = ParseNav(navFile)
WriteLog("NOTE: Navigator file: " + navFile + " (" + str(len(navItems)) + " items)")

# Which map was each point of the group added on?

groupPoints, mapItems = GroupItemsFromNav(navItems, groupID, groupStageX, groupStageY)
if groupPoints is not None:
    pointMaps = []
    for _, pointItem in groupPoints:
        if int(pointItem["DrawnID"][0]) not in pointMaps:
            pointMaps.append(int(pointItem["DrawnID"][0]))
    WriteLog("NOTE: " + str(len(groupPoints)) + " point(s) in this group, added on " + str(len(pointMaps)) + " map(s).")

    # The points on the loaded map have to get exactly the coordinates that SerialEM reports for the group, otherwise
    # the per-item coordinates (used for the points on the other maps) cannot be trusted either.

    onLoadedMap = [(index, item) for index, item in groupPoints if int(item["DrawnID"][0]) == drawnID]
    if len(onLoadedMap) != groupImageX.size or len(onLoadedMap) == 0:
        WriteLog("WARNING: The points of this group that were added on the loaded map (" + str(len(onLoadedMap)) + ") do not match the points SerialEM placed on it (" + str(groupImageX.size) + ").")
        groupPoints = None
    else:
        for k, (index, _) in enumerate(onLoadedMap):
            coords = PointImageCoords(index, buffer)
            if coords is None or abs(coords[0] - groupImageY[k]) > 2 or abs(coords[1] - groupImageX[k]) > 2:
                WriteLog("WARNING: The image coordinates that SerialEM reports for the group and for the individual items do not agree (point " + str(k + 1) + " of those on the loaded map).")
                groupPoints = None
                break

if groupPoints is None:
    WriteLog("WARNING: Falling back to the coordinates that SerialEM reports for the whole group on the loaded map; points added on other maps would be placed wrongly.")
    if dummy and (groupImageX.size != groupStageX.size or groupImageY.size != groupStageY.size):
        WriteLog("WARNING: In DUMMY mode SerialEM cannot place the group's points on the map; they are all placed in the centre of the map, which is what the DUMMY version of the script needs.")
        groupImageX = np.full(groupStageX.size, x / 2)
        groupImageY = np.full(groupStageY.size, y / 2)
    elif groupImageX.size != groupStageX.size or groupImageY.size != groupStageY.size:
        WriteLog("ERROR: Only " + str(groupImageX.size) + " of the " + str(groupStageX.size) + " points of this group could be placed on the map that was loaded (Navigator item " + str(drawnID) + ").")
        WriteLog("ERROR: This happens when the points of a group were added on different maps (e.g. on sections 2,3,4 of a multi-section view map), when the Navigator file and SerialEM disagree, or in DUMMY mode.")
        WriteLog("ERROR: Please add the targets of this group on one map, or split them into one group per map.")
        if sem.ReportIfNavAcquiring()[0]:
            sem.SkipAcquiringNavItem()
            sem.EndAcquireAtItems()
        sem.Exit()

# Calculate field of view
# The virtual maps get EXACTLY the dimensions and pixel size of the template images: the source image is cropped
# over the area that one template image covers and resized to the template's pixel dimensions.

if not onlyView:
    out_recX, out_recY = recordSizeX, recordSizeY

out_viewX, out_viewY = viewSizeX, viewSizeY

# Take the reference images

tgtImages = []
viewImages = []
targets = []
alignSign = 1                # direction of the image shift correction, inverted if a correction does not improve
viewName, previewName = None, None    # names of the reference files as they were written
mapRange = None              # value range of the map file, for the byte-converted images from the display buffer
stageBefore = None
loadedMapID = None
nativeItems = False
if liveReferences and not dummy and buffer == "A":
    nativeItems = True                   # SerialEM saves the images and makes the map entries itself
if liveReferences and not dummy:
    WriteLog("NOTE: MODE: liveReferences - taking a View and a Preview image at every point and re-aligning to the point in the map.")
    if nativeItems:
        WriteLog("NOTE: The View and Preview images will be saved by SerialEM and turned into map entries by it (NewMap), so the entries have SerialEM's own keys and point at the files it wrote.")
    else:
        WriteLog("WARNING: The images are not in buffer A (they are in buffer " + str(buffer) + "), so SerialEM cannot make the map entries from them; the entries are built by the script as before.")
elif liveReferences:
    WriteLog("WARNING: MODE: liveReferences is on, but SerialEM runs in DUMMY mode, where there is no live image to take. The reference images are therefore CROPPED out of the map, exactly as with liveReferences = False. The rest of the script (targets file, navigator items) still runs; to get real View and Preview references, run the script on the microscope.")
if not liveReferences:
    WriteLog("NOTE: MODE: liveReferences is off - cropping the reference images out of the map.")
if 0 < drawnID <= len(navItems) and "MapID" in navItems[drawnID - 1]:
    loadedMapID = int(navItems[drawnID - 1]["MapID"][0])
for i in range(groupStageX.size):
    WriteLog("Creating virtual map of point " + str(i + 1) + "...")

    # Load the map this point was added on, if it is not the one already loaded, and get its position in it
    # (in the liveReferences mode it is also needed after an acquisition has used the buffer, so it is always reloaded)

    if groupPoints is not None:
        pointIndex, pointItem = groupPoints[i]
        pointMapID = int(pointItem["DrawnID"][0])
        if pointMapID in mapItems and (pointMapID != loadedMapID or (liveReferences and not dummy)):
            sem.LoadOtherMap(mapItems[pointMapID], buffer)
            pixSize = sem.ImageProperties(buffer)[4] * 10
            image = np.asarray(sem.bufferImage(buffer), dtype=float)
            loadedMapID = pointMapID
        pointY, pointX = image.shape[0] / 2, image.shape[1] / 2
        if pointMapID == loadedMapID:
            coords = PointImageCoords(pointIndex, buffer)
            if coords is None:
                WriteLog("WARNING: The position of point " + str(i + 1) + " on its map could not be obtained; its reference images are centred on the position of the map.")
            else:
                pointY, pointX = coords
        else:
            WriteLog("WARNING: Point " + str(i + 1) + " was added on map ID " + str(pointMapID) + ", which is not in the Navigator file; using the loaded map.")
    else:
        pointY, pointX = groupImageY[i], groupImageX[i]

    if liveReferences and not dummy:
        # Take a real View image at the point and re-align to the position of the point in the map with the image shift:
        # a position taken from the map is not accurate enough, and the target has to sit in the centre of the reference
        # images, because those are what PACEtomo aligns the acquired images to. The Preview image is taken with the same
        # shift and is re-aligned as well, since it is the finer of the two references. The offsets are logged.

        if stageBefore is None:
            stageBefore = sem.ReportStageXYZ()
        WriteLog("NOTE: Taking a live View and Preview image at point " + str(i + 1) + " and re-aligning to its position in the map.")
        sem.MoveStageTo(groupStageX[i], groupStageY[i], stageZ)
        viewRef, viewRefPix, viewOffset = None, None, None
        previewRef, previewRefPix, previewOffset = None, None, None
        shifted = False
        previousOffset = None
        for attempt in range(3):
            sem.V()
            attemptPix = sem.ImageProperties(buffer)[4] * 10
            attemptImage = np.asarray(sem.bufferImage(buffer), dtype=float)
            dy, dx, quality, sharpness = FindOffsetInImage(image, (pointY, pointX), pixSize, attemptImage, attemptPix)
            if dy is None:
                WriteLog("WARNING: The target could not be located in the View image of point " + str(i + 1) + " (match quality " + str(round(quality, 2)) + ", peak ratio " + str(round(sharpness, 1)) + "); the reference images are taken at the requested position.")
                if viewRef is None:
                    viewRef, viewRefPix = attemptImage, attemptPix
                break
            offset = float(np.hypot(dy, dx)) * attemptPix / 1000
            WriteLog("NOTE: Point " + str(i + 1) + ", re-alignment attempt " + str(attempt + 1) + ": target is " + str(round(offset, 3)) + " um from the requested position (match quality " + str(round(quality, 2)) + ").")
            if viewOffset is None or offset < viewOffset:
                viewRef, viewRefPix, viewOffset = attemptImage, attemptPix, offset
            if offset <= refAlignTol or attempt == 2:
                break
            if previousOffset is not None and offset >= previousOffset:
                alignSign = -alignSign
                WriteLog("WARNING: The re-alignment of point " + str(i + 1) + " did not improve; inverting the direction of the image shift correction.")
            previousOffset = offset
            moveX, moveY = OffsetToStage(dy, dx, buffer)
            if moveX is None:
                WriteLog("WARNING: The image shift needed to re-align point " + str(i + 1) + " could not be calculated (BufImageToStageMatrix); the reference images are taken at the requested position.")
                break
            sem.ImageShiftByStageDiff(alignSign * moveX, alignSign * moveY)
            shifted = True

        # Save the View image with SerialEM while it is still in the buffer (the last one taken is the re-aligned one)

        viewSaved, viewItemIndex, viewName = None, None, None
        viewFileName = userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc"
        if viewRef is not None and viewRef is attemptImage and nativeItems:
            viewWritten, viewItemIndex = SaveAndMapReference(os.path.join(curDir, viewFileName), buffer, "v" + str(i + 1).zfill(2), viewFileName)
            if viewWritten is not None:
                viewSaved, viewName = viewWritten, os.path.basename(viewWritten)
        elif viewRef is not None and viewRef is attemptImage:
            viewSaved = SaveBufferWithSerialEM(os.path.join(curDir, viewFileName), buffer)
            viewName = os.path.basename(viewSaved) if viewSaved is not None else None
        elif viewRef is not None:
            WriteLog("WARNING: The best aligned View image of point " + str(i + 1) + " was not the last one taken, so it is written directly instead of being saved by SerialEM.")

        if not onlyView:
            for attempt in range(2):
                sem.L()
                attemptPix = sem.ImageProperties(buffer)[4] * 10
                attemptImage = np.asarray(sem.bufferImage(buffer), dtype=float)
                dy, dx, quality, sharpness = FindOffsetInImage(image, (pointY, pointX), pixSize, attemptImage, attemptPix)
                if dy is None:
                    WriteLog("WARNING: The target could not be located in the Preview image of point " + str(i + 1) + " (match quality " + str(round(quality, 2)) + "); the reference images are taken as they are.")
                    if previewRef is None:
                        previewRef, previewRefPix = attemptImage, attemptPix
                    break
                offset = float(np.hypot(dy, dx)) * attemptPix / 1000
                WriteLog("NOTE: Point " + str(i + 1) + ": target is " + str(round(offset, 3)) + " um from the centre of its Preview image (match quality " + str(round(quality, 2)) + ").")
                if previewOffset is None or offset < previewOffset:
                    previewRef, previewRefPix, previewOffset = attemptImage, attemptPix, offset
                if offset <= refAlignTol or attempt == 1:
                    break
                moveX, moveY = OffsetToStage(dy, dx, buffer)
                if moveX is None:
                    break
                sem.ImageShiftByStageDiff(alignSign * moveX, alignSign * moveY)
                shifted = True

        previewSaved, previewItemIndex, previewName = None, None, None
        previewFileName = userName + "_tgt_" + str(i + 1).zfill(3) + ".mrc"
        previewNote = (userName + "_tgts.txt") if i == 0 else previewFileName
        if previewRef is not None and previewRef is attemptImage and nativeItems:
            previewWritten, previewItemIndex = SaveAndMapReference(os.path.join(curDir, previewFileName), buffer, str(i + 1).zfill(3), previewNote, acquire=(i == 0))
            if previewWritten is not None:
                previewSaved, previewName = previewWritten, os.path.basename(previewWritten)
        elif previewRef is not None and previewRef is attemptImage:
            previewSaved = SaveBufferWithSerialEM(os.path.join(curDir, previewFileName), buffer)
            previewName = os.path.basename(previewSaved) if previewSaved is not None else None
        elif previewRef is not None:
            WriteLog("WARNING: The best aligned Preview image of point " + str(i + 1) + " was not the last one taken, so it is written directly instead of being saved by SerialEM.")

        if shifted:
            sem.ResetImageShift()       # reset the image shift, moving the stage to compensate, so that the field stays put

        if viewRef is None:
            WriteLog("ERROR: No View image could be taken at point " + str(i + 1) + ".")
        else:
            viewImageProc = np.flip(viewRef, axis=0).astype(np.float32)
            viewImages.append(viewImageProc)
            if viewSaved is None:
                WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", viewImageProc, viewRefPix)
            if viewRef.shape != (viewSizeY, viewSizeX) or abs(viewRefPix - viewPixSize) > 0.01:
                WriteLog("WARNING: The View image of point " + str(i + 1) + " is " + str(viewRef.shape[1]) + "x" + str(viewRef.shape[0]) + " at " + str(round(viewRefPix, 2)) + " A/px, but the View template has " + str(viewSizeX) + "x" + str(viewSizeY) + " at " + str(round(viewPixSize, 2)) + " A/px; check the low dose View settings.")
            if not onlyView and previewRef is not None:
                tgtImageProc = np.flip(previewRef, axis=0).astype(np.float32)
                tgtImages.append(tgtImageProc)
                if previewSaved is None:
                    WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + ".mrc", tgtImageProc, previewRefPix)
                if previewRef.shape != (recordSizeY, recordSizeX) or abs(previewRefPix - recordPixSize) > 0.01:
                    WriteLog("WARNING: The Preview image of point " + str(i + 1) + " is " + str(previewRef.shape[1]) + "x" + str(previewRef.shape[0]) + " at " + str(round(previewRefPix, 2)) + " A/px, but the Preview template has " + str(recordSizeX) + "x" + str(recordSizeY) + " at " + str(round(recordPixSize, 2)) + " A/px; check the low dose Preview/Record settings.")
    else:
        fovView = FovInSourcePixels(viewSizeX, viewSizeY, viewPixSize, pixSize)
        fovRec = FovInSourcePixels(recordSizeX, recordSizeY, recordPixSize, pixSize) if not onlyView else None
        edgeTarget = not CropFits(image, (pointY, pointX), fovView) or (not onlyView and not CropFits(image, (pointY, pointX), fovRec))

        # A target too close to the edge of the map cannot be cropped without padding. Padding produces a flat area with
        # a hard edge, which biases the realignment during the collection, since PACEtomo aligns to these references.

        if edgeTarget:
            WriteLog("WARNING: Point " + str(i + 1) + " is too close to the edge of the map and its reference images are padded with artificial pixels which can bias the realignment.")

        # SerialEM loads maps converted to bytes for display ("Convert Maps to Bytes", on by default), so the image in the
        # buffer has a range of 0..255 and not the values that were written to the map file. A reference image made from
        # it therefore looks dim next to an image SerialEM wrote from the camera (the range in the session that was
        # checked was 0..255 against 0..12576 in the file). Rescale the reference onto the value range stored in the map
        # file, so that it has the same values and clips the same way as a camera image.

        if mapRange is None and not dummy:
            mapIndex = mapItems.get(pointMapID) if groupPoints is not None else drawnID
            mapRange = ReadMrcRange(MapFilePath(navItems, mapIndex))
            if mapRange is not None:
                WriteLog("NOTE: The reference images are scaled to the value range of the map file (" + str(round(mapRange[0], 2)) + " to " + str(round(mapRange[1], 2)) + "), because SerialEM loads maps converted to bytes for display.")

        if not onlyView:
            imageCrop = CropImage(image, (pointY, pointX), fovRec)
            imageProc = np.flip(resize(imageCrop, (out_recY, out_recX), preserve_range=True, anti_aliasing=True).astype(np.float32), axis=0)
            if mapRange is not None:
                imageProc = (mapRange[0] + imageProc * (mapRange[1] - mapRange[0]) / 255.0).astype(np.float32)
            tgtImages.append(imageProc)
            WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + ".mrc", tgtImages[i], recordPixSize)

        imageCrop = CropImage(image, (pointY, pointX), fovView)
        viewImageProc = np.flip(resize(imageCrop, (out_viewY, out_viewX), preserve_range=True, anti_aliasing=True).astype(np.float32), axis=0)
        if mapRange is not None:
            viewImageProc = (mapRange[0] + viewImageProc * (mapRange[1] - mapRange[0]) / 255.0).astype(np.float32)
        viewImages.append(viewImageProc)
        WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", viewImageProc, viewPixSize)

    if not dummy:
        SSX, SSY = s2ssMatrix @ np.array([groupStageX[i] - groupStageX[0], groupStageY[i] - groupStageY[0]])
        if SSX > 15 or SSY > 15:
            WriteLog("WARNING: Point " + str(i + 1) + " requires image shifts (" + str(round(SSX, 1)) + "|" + str(round(SSY, 1)) + ") beyond the default image shift limit (15)!")
        tgt_params = {"tsfile": userName + "_ts_" + str(i + 1).zfill(3) + ".mrc", 
                    "viewfile": viewName if viewName else userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", 
                    "SSX": SSX, 
                    "SSY": SSY, 
                    "stageX": groupStageX[i], 
                    "stageY": groupStageY[i], 
                    "skip": "False"}
    else:       # leave out SS coords and calculate from stage coords on the fly in PACEtomo.py
        tgt_params = {"tsfile": userName + "_ts_" + str(i + 1).zfill(3) + ".mrc", 
                    "viewfile": viewName if viewName else userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", 
                    "stageX": groupStageX[i], 
                    "stageY": groupStageY[i], 
                    "skip": "False"}
    if not onlyView:
        tgt_params["tgtfile"] = previewName if previewName else userName + "_tgt_" + str(i + 1).zfill(3) + ".mrc"
    targets.append(tgt_params)

if not onlyView:
    templatePrev = copy.deepcopy(navItems[prevID - 1])
    templatePrev.pop("RawStageXY", None)
    templatePrev.pop("SamePosId", None)

templateView = copy.deepcopy(navItems[viewID - 1])
templateView.pop("RawStageXY", None)
templateView.pop("SamePosId", None)

# Determine image dimenstions for polygons

if not onlyView:
    ptsDX_rec = np.array(np.array(templatePrev["PtsX"], dtype=float) - float(templatePrev["StageXYZ"][0]))
    ptsDY_rec = np.array(np.array(templatePrev["PtsY"], dtype=float) - float(templatePrev["StageXYZ"][1]))
ptsDX_view = np.array(np.array(templateView["PtsX"], dtype=float) - float(templateView["StageXYZ"][0]))
ptsDY_view = np.array(np.array(templateView["PtsY"], dtype=float) - float(templateView["StageXYZ"][1]))

if not nativeItems:
    # The entries are built here by the script only when the live mode could not have SerialEM make them itself.

    navItemsExisting = len(navItems)
    WriteLog("NOTE: Adding the maps of the references of every target to the Navigator.")

    # Add maps

    newNavIDs = []
    for i, tgt in enumerate(targets):
    
        # Create view map first to get mapID
        viewItem = copy.deepcopy(templateView)
        viewItem["Item"] = "v" + str(i + 1).zfill(2)
        viewItem["StageXYZ"] = [tgt["stageX"], tgt["stageY"], stageZ]
        viewItem["PtsX"] = ptsDX_view + tgt["stageX"]
        viewItem["PtsY"] = ptsDY_view + tgt["stageY"]
        viewItem["MapFile"] = [os.path.join(curDir, tgt["viewfile"])]
        viewItem["Note"] = [tgt["viewfile"]]
        viewItem["MapBinning"] = [int(viewBinning * binFactor)]        # an integer key: a float such as 2.0 makes the whole item unreadable
        viewItem["MapMinMaxScale"] = [np.min(viewImages[i]), np.max(viewImages[i])]

        # Create new map ID
        uniqueID = int(sem.GetUniqueNavID())
        while uniqueID in newNavIDs:
            uniqueID = int(sem.GetUniqueNavID())
        newNavIDs.append(uniqueID)

        newViewID = uniqueID

        viewItem["MapID"] = [newViewID]
        viewItem["RealignedID"] = [drawnID]
        viewItem["SamePosId"] = [newViewID]

        if not onlyView:
            # Create target map
            tgtItem = copy.deepcopy(templatePrev)
            tgtItem["Item"] = str(i + 1).zfill(3)
            tgtItem["StageXYZ"] = [tgt["stageX"], tgt["stageY"], stageZ]
            tgtItem["PtsX"] = ptsDX_rec + tgt["stageX"]
            tgtItem["PtsY"] = ptsDY_rec + tgt["stageY"]
            tgtItem["MapFile"] = [os.path.join(curDir, tgt["tgtfile"])]
            tgtItem["MapBinning"] = [int(recordBinning * binFactor)]    # an integer key, see above
            tgtItem["MapMinMaxScale"] = [np.min(tgtImages[i]), np.max(tgtImages[i])]

            # Create new map ID
            uniqueID = int(sem.GetUniqueNavID())
            while uniqueID in newNavIDs:
                uniqueID = int(sem.GetUniqueNavID())
            newNavIDs.append(uniqueID)

            tgtItem["MapID"] = [uniqueID]
            tgtItem["RealignedID"] = [drawnID]
            tgtItem["SamePosId"] = [newViewID]

            if i == 0:
                tgtItem["Note"] = [userName + "_tgts.txt"]
                tgtItem["Acquire"] = [1]
            else:
                tgtItem["Note"] = [tgt["tgtfile"]]
                tgtItem["Acquire"] = [0]

            navItems.append(tgtItem)
        else:
            if i == 0:
                viewItem["Note"] = [userName + "_tgts.txt"]
                viewItem["Acquire"] = [1]
            else:
                viewItem["Note"] = [tgt["viewfile"]]
                viewItem["Acquire"] = [0]

        navItems.append(viewItem)

if stageBefore is not None:
    WriteLog("NOTE: Returning to the stage position the script started at.")
    sem.MoveStageTo(*stageBefore)

# Write tgts file

# The maps and the targets file are the result of this script and are written first, because updating the Navigator can
# fail for reasons that have nothing to do with this run: SerialEM refuses to read a Navigator file when any of its
# items refers to a map file that cannot be found or opened (e.g. items of an earlier run whose maps were moved).

WriteTargets(tgtsFilePath, targets)

if noUI and navID > 0:
    # In a batch (noUI) the script was run at the point that markGroupFirstPoints marked to start this group. That
    # point is prepared now, so the Acquire flag moves on to the map of target 1: the next Acquire at Items pass can
    # then run the acquisition at the groups that are ready.

    try:
        sem.SetItemAcquire(navID, 0)
        WriteLog("NOTE: The Acquire flag was moved from this point to the map of target 1, so the next Acquire at Items pass runs the acquisition.")
    except Exception as e:
        WriteLog("WARNING: The Acquire flag of the point this script ran on could not be cleared (" + str(e) + "); clear it by hand so that the point is not acquired again.")

if nativeItems:
    # The map entries of the references are already in the Navigator, made by SerialEM, so the file only has to be
    # saved. Nothing is rewritten and nothing is read back in, so no unreadable item anywhere in the file can keep
    # these entries out of the Navigator.

    try:
        sem.SaveNavigator(navFile + "~")                # backup of the unaltered Navigator file
        sem.SaveNavigator()
        WriteLog("NOTE: The Navigator file was saved with the " + str(len(targets)) + " new maps in it.")
    except Exception as e:
        WriteLog("WARNING: Saving the Navigator file failed: " + str(e))
        WriteLog("NOTE: The maps and the targets file were written and the maps are in the Navigator in memory; save the Navigator by hand (Navigator - Save).")
else:
    # Add the new items to the Navigator

    # They are NOT added by rewriting the whole Navigator file and reading it back in: SerialEM refuses to read a Navigator
    # file when ANY of its items refers to a map file that cannot be found or opened, and that would keep the reference maps
    # of this run out of the Navigator as well. The new items are written to a separate file and merged instead, because the
    # merge reads only that file, and the Navigator is then saved.

    newItemsFile = navFile + ".new"
    newItems = navItems[navItemsExisting:]
    try:
        sem.SaveNavigator(navFile + "~")                    # backup unaltered nav file
    except Exception as e:
        WriteLog("WARNING: The backup of the Navigator file could not be written: " + str(e))

    merged = False
    try:
        WriteNav(navHeader, newItems, newItemsFile)
        sem.MergeNavFile(newItemsFile)
        sem.SaveNavigator()
        merged = True
        WriteLog("NOTE: " + str(len(newItems)) + " items were merged into the Navigator and the Navigator file was saved. The items are also in " + newItemsFile + ".")
        try:
            os.remove(newItemsFile)
        except Exception:
            pass
    except Exception as e:
        WriteLog("WARNING: Adding the new maps to the Navigator failed: " + str(e))
        WriteLog("WARNING: The " + str(len(newItems)) + " new items are in " + newItemsFile + " and can be merged by hand with Navigator - Merge File.")
        WriteLog("WARNING: SerialEM does not read a Navigator file when one of its items refers to a map file that cannot be found or opened; this can be an item of an earlier run or one of the reference files written by this script.")
        WriteLog("NOTE: The maps and the targets file were written before this step: " + tgtsFilePath)
        try:
            WriteNav(navHeader, navItems, navFile)      # fallback: the previous way, all items in the whole file
            sem.ReadNavFile(navFile)
            WriteLog("NOTE: The whole Navigator file was written and read back in instead (fallback).")
        except Exception as e:
            WriteLog("WARNING: That failed as well: " + str(e))
        if not dummy:
            # find out whether the files written by this script are the problem, by reading one of them back with SerialEM
            checkBuffers = ["A"] if onlyView else ["A", "B"]
            checkFiles = [userName + "_tgt_001_view.mrc"] if onlyView else [userName + "_tgt_001_view.mrc", userName + "_tgt_001.mrc"]
            for checkFile, checkBuffer in zip(checkFiles, checkBuffers):
                try:
                    sem.OpenOldFile(os.path.join(curDir, checkFile))
                    sem.ReadFile(0, checkBuffer)
                    sem.CloseFile()
                    WriteLog("NOTE: Read-back check passed for " + checkFile + ".")
                except Exception as e:
                    WriteLog("ERROR: SerialEM cannot read back the file written by this script: " + checkFile + " (" + str(e) + ")")
                    WriteLog("ERROR: Navigator items that point to such a file cannot be loaded either; this is what is reported as 'items could not be read in from Navigator file'. Please report this message together with the file.")
                try:
                    sem.CloseFile()
                except Exception:
                    pass

WriteLog("Target selection completed! " + str(len(targets)) + " targets were selected.")

