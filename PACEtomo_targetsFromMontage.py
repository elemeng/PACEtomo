#!Python
# ===================================================================
#ScriptName     PACEtomo_targetsFromMontage
# Author:       Fabian Eisenstein
# Created:      2022/12/09
# Revision:     v1.12
# Last Change:  2026/10/15: added the liveReferences mode: a real View and Preview image are taken at every point and
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
noUI    = False     # set to True to avoid folder/name selection (e.g. to run the script in a Acquire at Items routine), it will use the label of the montage as name template
prefix  = "pos"     # prefix for name when running noUI
liveReferences = True      # True: take a real View and Preview image at every point and use them as the references of
                    # that target (like PACEtomo_selectTargets), re-aligning to the point in the map first. The
                    # references then contain the resolution of the live images instead of the map, at the cost of one
                    # View and one Preview exposure per point. False: crop the references out of the montage map.
refAlignTol = 0.05  # target position tolerance [microns] of the re-alignment in the liveReferences mode; the target is
                    # re-aligned with the image shift until it is centred within this distance

### END SETTINGS ###

import serialem as sem
import os
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

def WriteNav(header, items, filename):
    text = ""
    for line in header:
        text += line
    text += os.linesep
    for item in items:
        text += "[Item = " + item["Item"] + "]" + os.linesep
        item.pop("Item")
        item.pop("index")
        for key, attr in item.items():
            text += key + " = "
            for val in attr:
                text += str(val) + " "
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
        sem.Echo("WARNING: Target position is close to the edge of the map and was padded.")
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

s2ssMatrix = None
dummy = False
if sem.ReportProperty("DummyInstance") == 1:
    if not onlyView:
        sem.Echo("WARNING: You are using SerialEM in DUMMY mode. Make sure a View image and Preview image template are available in the Navigator!")
    else:
        sem.Echo("WARNING: You are using SerialEM in DUMMY mode. Make sure a View image template is available in the Navigator!")
    dummy = True

if onlyView:
    sem.Echo("NOTE: Running in View-only mode: Only View maps will be created. Please change the setting in the script if you want to create Preview maps as well.")

sem.Echo("NOTE: PACEtomo_targetsFromMontage, Revision v1.11 (2026/10/15). If this line does not show the version you expect, the script that ran is not this file.")

# Get nav item info

navID = int(sem.ReportNavItem()[0])
groupInfo = sem.ReportGroupStatus()
groupID = int(groupInfo[1])
drawnID = int(sem.NavIndexItemDrawnOn(navID))

if drawnID == 0:
    if sem.ReportIfNavAcquiring()[0]:
        sem.SkipAcquiringNavItem()
        sem.EndAcquireAtItems()     # only available from 12-May-23
        sem.Echo("NOTE: This nav item is not a point on a map and will be ignored.")
        sem.Exit()
    else:
        sem.Echo("ERROR: This nav item is not a point on a map. Make sure to save an image as a map prior to adding points!")
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

# Make sure tgts file is unique
counter = 1
while os.path.exists(tgtsFilePath):
    counter += 1
    tgtsFilePath = os.path.join(curDir, userName + str(counter) + "_tgts.txt") 
if counter > 1:
    userName = userName + str(counter)
    sem.Echo("NOTE: A targets file with this name already exists, using the name " + userName + "_tgts.txt for this run.")

sem.Echo("NOTE: Targets file of this run: " + tgtsFilePath)

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
        sem.Echo("ERROR: Template for View or Preview image was not found!")
        sem.Echo("If you have a View and Preview image in the Navigator that you want to use as template, please change the Navigator Note to 'Template View' and 'Template Preview', respectively.")
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
        sem.Echo("ERROR: Template for View or Preview show the same pixel size! Make sure the maps could be loaded properly!")
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
    sem.Echo("ERROR: Only " + str(groupImageX.size) + " of the " + str(groupStageX.size) + " points of this group could be placed on the map that was loaded (Navigator item " + str(drawnID) + ").")
    sem.Echo("ERROR: All points of a group have to be added on the same map, i.e. on the same section of the same file. Points added on other maps or on other sections of a multi-section image cannot be cropped.")
    sem.Echo("ERROR: Please add the targets of this group on one map, or split them into one group per map.")
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
    sem.Echo("ERROR: There is no Navigator file defined (" + str(e) + ").")
    sem.Echo("ERROR: Save the Navigator with Navigator - Save as / Save before running this script; without a file the points and their maps cannot be read.")
    if sem.ReportIfNavAcquiring()[0]:
        sem.SkipAcquiringNavItem()
        sem.EndAcquireAtItems()
    sem.Exit()
navHeader, navItems = ParseNav(navFile)
sem.Echo("NOTE: Navigator file: " + navFile + " (" + str(len(navItems)) + " items)")

# Which map was each point of the group added on?

groupPoints, mapItems = GroupItemsFromNav(navItems, groupID, groupStageX, groupStageY)
if groupPoints is not None:
    pointMaps = []
    for _, pointItem in groupPoints:
        if int(pointItem["DrawnID"][0]) not in pointMaps:
            pointMaps.append(int(pointItem["DrawnID"][0]))
    sem.Echo("NOTE: " + str(len(groupPoints)) + " point(s) in this group, added on " + str(len(pointMaps)) + " map(s).")

    # The points on the loaded map have to get exactly the coordinates that SerialEM reports for the group, otherwise
    # the per-item coordinates (used for the points on the other maps) cannot be trusted either.

    onLoadedMap = [(index, item) for index, item in groupPoints if int(item["DrawnID"][0]) == drawnID]
    if len(onLoadedMap) != groupImageX.size or len(onLoadedMap) == 0:
        sem.Echo("WARNING: The points of this group that were added on the loaded map (" + str(len(onLoadedMap)) + ") do not match the points SerialEM placed on it (" + str(groupImageX.size) + ").")
        groupPoints = None
    else:
        for k, (index, _) in enumerate(onLoadedMap):
            coords = PointImageCoords(index, buffer)
            if coords is None or abs(coords[0] - groupImageY[k]) > 2 or abs(coords[1] - groupImageX[k]) > 2:
                sem.Echo("WARNING: The image coordinates that SerialEM reports for the group and for the individual items do not agree (point " + str(k + 1) + " of those on the loaded map).")
                groupPoints = None
                break

if groupPoints is None:
    sem.Echo("WARNING: Falling back to the coordinates that SerialEM reports for the whole group on the loaded map; points added on other maps would be placed wrongly.")
    if dummy and (groupImageX.size != groupStageX.size or groupImageY.size != groupStageY.size):
        sem.Echo("WARNING: In DUMMY mode SerialEM cannot place the group's points on the map; they are all placed in the centre of the map, which is what the DUMMY version of the script needs.")
        groupImageX = np.full(groupStageX.size, x / 2)
        groupImageY = np.full(groupStageY.size, y / 2)
    elif groupImageX.size != groupStageX.size or groupImageY.size != groupStageY.size:
        sem.Echo("ERROR: Only " + str(groupImageX.size) + " of the " + str(groupStageX.size) + " points of this group could be placed on the map that was loaded (Navigator item " + str(drawnID) + ").")
        sem.Echo("ERROR: This happens when the points of a group were added on different maps (e.g. on sections 2,3,4 of a multi-section view map), when the Navigator file and SerialEM disagree, or in DUMMY mode.")
        sem.Echo("ERROR: Please add the targets of this group on one map, or split them into one group per map.")
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
stageBefore = None
loadedMapID = None
if liveReferences and not dummy:
    sem.Echo("NOTE: liveReferences is on: taking a View and a Preview image at every point and re-aligning to the point in the map.")
elif liveReferences:
    sem.Echo("WARNING: liveReferences is on but SerialEM runs in DUMMY mode: cropping the references out of the map instead.")
if 0 < drawnID <= len(navItems) and "MapID" in navItems[drawnID - 1]:
    loadedMapID = int(navItems[drawnID - 1]["MapID"][0])
for i in range(groupStageX.size):
    sem.Echo("Creating virtual map of point " + str(i + 1) + "...")

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
                sem.Echo("WARNING: The position of point " + str(i + 1) + " on its map could not be obtained; its reference images are centred on the position of the map.")
            else:
                pointY, pointX = coords
        else:
            sem.Echo("WARNING: Point " + str(i + 1) + " was added on map ID " + str(pointMapID) + ", which is not in the Navigator file; using the loaded map.")
    else:
        pointY, pointX = groupImageY[i], groupImageX[i]

    if liveReferences and not dummy:
        # Take a real View image at the point and re-align to the position of the point in the map with the image shift:
        # a position taken from the map is not accurate enough, and the target has to sit in the centre of the reference
        # images, because those are what PACEtomo aligns the acquired images to. The Preview image is taken with the same
        # shift and is re-aligned as well, since it is the finer of the two references. The offsets are logged.

        if stageBefore is None:
            stageBefore = sem.ReportStageXYZ()
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
                sem.Echo("WARNING: The target could not be located in the View image of point " + str(i + 1) + " (match quality " + str(round(quality, 2)) + ", peak ratio " + str(round(sharpness, 1)) + "); the reference images are taken at the requested position.")
                if viewRef is None:
                    viewRef, viewRefPix = attemptImage, attemptPix
                break
            offset = float(np.hypot(dy, dx)) * attemptPix / 1000
            sem.Echo("NOTE: Point " + str(i + 1) + ", re-alignment attempt " + str(attempt + 1) + ": target is " + str(round(offset, 3)) + " um from the requested position (match quality " + str(round(quality, 2)) + ").")
            if viewOffset is None or offset < viewOffset:
                viewRef, viewRefPix, viewOffset = attemptImage, attemptPix, offset
            if offset <= refAlignTol or attempt == 2:
                break
            if previousOffset is not None and offset >= previousOffset:
                alignSign = -alignSign
                sem.Echo("WARNING: The re-alignment of point " + str(i + 1) + " did not improve; inverting the direction of the image shift correction.")
            previousOffset = offset
            moveX, moveY = OffsetToStage(dy, dx, buffer)
            if moveX is None:
                sem.Echo("WARNING: The image shift needed to re-align point " + str(i + 1) + " could not be calculated (BufImageToStageMatrix); the reference images are taken at the requested position.")
                break
            sem.ImageShiftByStageDiff(alignSign * moveX, alignSign * moveY)
            shifted = True

        if not onlyView:
            for attempt in range(2):
                sem.L()
                attemptPix = sem.ImageProperties(buffer)[4] * 10
                attemptImage = np.asarray(sem.bufferImage(buffer), dtype=float)
                dy, dx, quality, sharpness = FindOffsetInImage(image, (pointY, pointX), pixSize, attemptImage, attemptPix)
                if dy is None:
                    sem.Echo("WARNING: The target could not be located in the Preview image of point " + str(i + 1) + " (match quality " + str(round(quality, 2)) + "); the reference images are taken as they are.")
                    if previewRef is None:
                        previewRef, previewRefPix = attemptImage, attemptPix
                    break
                offset = float(np.hypot(dy, dx)) * attemptPix / 1000
                sem.Echo("NOTE: Point " + str(i + 1) + ": target is " + str(round(offset, 3)) + " um from the centre of its Preview image (match quality " + str(round(quality, 2)) + ").")
                if previewOffset is None or offset < previewOffset:
                    previewRef, previewRefPix, previewOffset = attemptImage, attemptPix, offset
                if offset <= refAlignTol or attempt == 1:
                    break
                moveX, moveY = OffsetToStage(dy, dx, buffer)
                if moveX is None:
                    break
                sem.ImageShiftByStageDiff(alignSign * moveX, alignSign * moveY)
                shifted = True

        if shifted:
            sem.ResetImageShift()       # reset the image shift, moving the stage to compensate, so that the field stays put

        if viewRef is None:
            sem.Echo("ERROR: No View image could be taken at point " + str(i + 1) + ".")
        else:
            viewImageProc = np.flip(viewRef, axis=0).astype(np.float32)
            viewImages.append(viewImageProc)
            WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", viewImageProc, viewRefPix)
            if viewRef.shape != (viewSizeY, viewSizeX) or abs(viewRefPix - viewPixSize) > 0.01:
                sem.Echo("WARNING: The View image of point " + str(i + 1) + " is " + str(viewRef.shape[1]) + "x" + str(viewRef.shape[0]) + " at " + str(round(viewRefPix, 2)) + " A/px, but the View template has " + str(viewSizeX) + "x" + str(viewSizeY) + " at " + str(round(viewPixSize, 2)) + " A/px; check the low dose View settings.")
            if not onlyView and previewRef is not None:
                tgtImageProc = np.flip(previewRef, axis=0).astype(np.float32)
                tgtImages.append(tgtImageProc)
                WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + ".mrc", tgtImageProc, previewRefPix)
                if previewRef.shape != (recordSizeY, recordSizeX) or abs(previewRefPix - recordPixSize) > 0.01:
                    sem.Echo("WARNING: The Preview image of point " + str(i + 1) + " is " + str(previewRef.shape[1]) + "x" + str(previewRef.shape[0]) + " at " + str(round(previewRefPix, 2)) + " A/px, but the Preview template has " + str(recordSizeX) + "x" + str(recordSizeY) + " at " + str(round(recordPixSize, 2)) + " A/px; check the low dose Preview/Record settings.")
    else:
        fovView = FovInSourcePixels(viewSizeX, viewSizeY, viewPixSize, pixSize)
        fovRec = FovInSourcePixels(recordSizeX, recordSizeY, recordPixSize, pixSize) if not onlyView else None
        edgeTarget = not CropFits(image, (pointY, pointX), fovView) or (not onlyView and not CropFits(image, (pointY, pointX), fovRec))

        # A target too close to the edge of the map cannot be cropped without padding. Padding produces a flat area with
        # a hard edge, which biases the realignment during the collection, since PACEtomo aligns to these references.

        if edgeTarget:
            sem.Echo("WARNING: Point " + str(i + 1) + " is too close to the edge of the map and its reference images are padded with artificial pixels which can bias the realignment.")

        if not onlyView:
            imageCrop = CropImage(image, (pointY, pointX), fovRec)
            imageProc = np.flip(resize(imageCrop, (out_recY, out_recX), preserve_range=True, anti_aliasing=True).astype(np.float32), axis=0)
            tgtImages.append(imageProc)
            WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + ".mrc", tgtImages[i], recordPixSize)

        imageCrop = CropImage(image, (pointY, pointX), fovView)
        viewImageProc = np.flip(resize(imageCrop, (out_viewY, out_viewX), preserve_range=True, anti_aliasing=True).astype(np.float32), axis=0)
        viewImages.append(viewImageProc)
        WriteMrc(userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", viewImageProc, viewPixSize)

    if not dummy:
        SSX, SSY = s2ssMatrix @ np.array([groupStageX[i] - groupStageX[0], groupStageY[i] - groupStageY[0]])
        if SSX > 15 or SSY > 15:
            sem.Echo("WARNING: Point " + str(i + 1) + " requires image shifts (" + str(round(SSX, 1)) + "|" + str(round(SSY, 1)) + ") beyond the default image shift limit (15)!")
        tgt_params = {"tsfile": userName + "_ts_" + str(i + 1).zfill(3) + ".mrc", 
                    "viewfile": userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", 
                    "SSX": SSX, 
                    "SSY": SSY, 
                    "stageX": groupStageX[i], 
                    "stageY": groupStageY[i], 
                    "skip": "False"}
    else:       # leave out SS coords and calculate from stage coords on the fly in PACEtomo.py
        tgt_params = {"tsfile": userName + "_ts_" + str(i + 1).zfill(3) + ".mrc", 
                    "viewfile": userName + "_tgt_" + str(i + 1).zfill(3) + "_view.mrc", 
                    "stageX": groupStageX[i], 
                    "stageY": groupStageY[i], 
                    "skip": "False"}
    if not onlyView:
        tgt_params["tgtfile"] = userName + "_tgt_" + str(i + 1).zfill(3) + ".mrc"
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
    viewItem["MapBinning"] = [viewBinning * binFactor]
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
        tgtItem["MapBinning"] = [recordBinning * binFactor]
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
    sem.Echo("NOTE: Returning to the stage position the script started at.")
    sem.MoveStageTo(*stageBefore)

# Write tgts file

# The maps and the targets file are the result of this script and are written first, because updating the Navigator can
# fail for reasons that have nothing to do with this run: SerialEM refuses to read a Navigator file when any of its
# items refers to a map file that cannot be found or opened (e.g. items of an earlier run whose maps were moved).

WriteTargets(tgtsFilePath, targets)

# Write nav file

try:
    sem.SaveNavigator(navFile + "~")        # backup unaltered nav file
    WriteNav(navHeader, navItems, navFile)
    sem.ReadNavFile(navFile)
    sem.Echo("Updated navigator file with target maps.")
except Exception as e:
    sem.Echo("WARNING: Updating the navigator file failed: " + str(e))
    sem.Echo("WARNING: SerialEM does not read a Navigator file when one of its items refers to a map file that cannot be found or opened. Remove the reported items (or move their map files into the folder of the current Navigator file) and reload the Navigator file.")
    sem.Echo("NOTE: The maps and the targets file were already written: " + tgtsFilePath)
    sem.Echo("NOTE: The Navigator in memory was not updated and the Navigator file on disk may be incomplete; its previous content is in " + navFile + "~")

sem.Echo("Target selection completed! " + str(len(targets)) + " targets were selected.")

