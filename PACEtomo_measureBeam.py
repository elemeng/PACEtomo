#!Python
# ===================================================================
#ScriptName     PACEtomo_measureBeam
# Purpose:      Tests the beam centering mechanism before using it inside PACEtomo. Reports the current
#               beam shift and either runs SerialEM's beam auto-centering (AutoCenterBeam) or centers
#               from an image (CenterBeamFromImage) `iterations` times, reporting what moved, how far,
#               how long it took and whether the routine converged - or only monitors the beam shift
#               over time (no images, no beam movement) to see how fast the beam drifts on this scope.
#               More information at http://github.com/eisfabian/PACEtomo
# Author:       local helper for the PACEtomo fork
# Created:      2026/09/30
# Revision:     v1.0
# Last Change:  2026/09/30: initial version
# ===================================================================

############ SETTINGS ############

mode            = "test"    # "test": report the beam shift and run the centering routine `iterations` times;
                            # "monitor": only log the beam shift every `monitorInterval` minutes for `monitorMinutes` minutes (no images, no movement)
method          = "auto"    # "auto": SerialEM's AutoCenterBeam (finds the beam itself, also works when the beam is larger than the camera)
                            # "edges": CenterBeamFromImage on a fresh View image, detecting the beam edges
                            # "centroid": CenterBeamFromImage using the intensity centroid (the beam must fit completely into the camera field)
iterations      = 3         # test mode: how often the centering is repeated (shows whether it converges, i.e. how much is left after the first run)
maxShift        = 5         # safety limit [um]: the beam is not moved if the routine determines a larger shift than this (both routines take it)
viewReduction   = -1        # AutoCenterBeam's #P: -1 = do not pass it (normal procedure); 0 or more = center using View with this percentage reduction in beam size
useLowDoseView  = True      # switch to the View low dose area first (recommended: the beam is usually small enough to be visible there)
cameraAreaFull  = True      # set the View camera area to Full during the test (helps the beam fit into the image); restored afterwards
settleDelay     = 1         # settling delay [s] after switching the low dose area and before an image
measureSize     = True      # also report the beam diameter [um] (MeasureBeamSize on the View image; may fail when no beam edges are visible)
returnToLowDoseArea = "R"   # low dose area to switch back to at the end (the acquisition area); "" keeps the View area
restoreShift    = False     # True: restore the beam shift measured at the start (do not keep the result of the centering)
monitorMinutes  = 60        # monitor mode: how long to watch the beam shift
monitorInterval = 5         # monitor mode: minutes between two samples

debug           = False     # Enables additional output for a few processes

########## END SETTINGS ##########

versionPB = "1.0"

import serialem as sem
from datetime import datetime

def log(text, color=0, style=0):
    if text.startswith("DEBUG:") and not debug:
        return
    if text.startswith("NOTE:"):
        color = 4
    elif text.startswith("WARNING:"):
        color = 5
    elif text.startswith("ERROR:"):
        color = 2
        style = 1
    elif text.startswith("DEBUG:"):
        color = 1
    if sem.IsVersionAtLeast("40200", "20240205"):
        sem.SetNextLogOutputStyle(style, color)
    sem.EchoBreakLines(text)

def firstValue(report):
    """First reported value, whatever the Python module returned for the command."""
    if isinstance(report, (tuple, list)):
        return report[0]
    return report

def reportShift():
    """Current beam shift in nominal microns as (x, y)."""
    x, y, *_ = sem.ReportBeamShift()
    return float(x), float(y)

def reportBeamSize():
    """Beam diameter [um] measured on the image in A, or None if no beam edges were found."""
    try:
        return float(firstValue(sem.MeasureBeamSize("A")))
    except Exception as e:
        log(f"DEBUG: Beam diameter could not be measured ({e}).")
        return None

def reportFOV():
    """Field of view [um] of the image in A, plus the pixel size [nm] and the binning for the log."""
    sizeX, sizeY, binning, exp, pixSize, *_ = sem.ImageProperties("A")
    return float(sizeX) * float(pixSize) / 1000.0, float(pixSize), int(binning)

def checkValves():
    if not int(sem.ReportColumnOrGunValve()):
        log("NOTE: The column/gun valve was closed. Opening it.")
        sem.SetColumnOrGunValve(1)

def reportState():
    x, y = reportShift()
    mag, *_ = sem.ReportMag()
    screen = int(firstValue(sem.ReportScreen()))
    log(f"Beam shift: x = {round(x, 3)} um | y = {round(y, 3)} um | mag: {mag}x | "
        f"low dose: {'on, area ' + str(ldArea) if ldOn == 1 else 'off'} | screen: {'down' if screen == 1 else 'UP (the beam is blocked!)'}")
    return x, y

def centerBeam():
    """One centering call. Returns (moved_x, moved_y, status_text, seconds)."""
    x0, y0 = reportShift()
    start = sem.ReportClock()
    status = ""
    sem.NoMessageBoxOnError(1)
    try:
        if method == "auto":
            if viewReduction >= 0 and ldOn == 1:
                sem.AutoCenterBeam(maxShift, viewReduction)
            else:
                if viewReduction >= 0:
                    log("WARNING: viewReduction requires low dose mode, which is off. Running AutoCenterBeam without it.")
                sem.AutoCenterBeam(maxShift)
            status = "AutoCenterBeam reports no status; compare the beam shift and the time it took"
        else:
            centroid = 1 if method == "centroid" else 0
            sem.V()
            sem.Delay(settleDelay, "s")
            if measureSize:
                fov, pixSize, binning = reportFOV()
                size = reportBeamSize()
                counts = round(float(firstValue(sem.ReportMeanCounts())), 1)
                if size is not None:
                    log(f"Beam diameter {round(size, 2)} um in a field of view of {round(fov, 2)} um "
                        f"(beam/FOV = {round(size / fov, 2)}x, {round(pixSize, 2)} nm/px bin {binning}, mean counts {counts})")
                else:
                    log(f"WARNING: No beam edge was found in a field of view of {round(fov, 2)} um "
                        f"({round(pixSize, 2)} nm/px bin {binning}, mean counts {counts}): the beam probably covers the whole image. "
                        f"An edge fit cannot determine its center from that - use the View low dose area or AutoCenterBeam with #P (reduced beam size).")
            code = int(firstValue(sem.CenterBeamFromImage(centroid, maxShift)))
            status = {0: "moved", -1: "no beam edges detected", 5: "circle fit to the beam edges failed",
                      6: "not moved: radius too high", 7: "not moved: fitting error too high"}.get(code, f"code {code}")
    except Exception as e:
        status = f"FAILED ({e})"
    finally:
        sem.NoMessageBoxOnError(0)
    x1, y1 = reportShift()
    return x1 - x0, y1 - y0, status, sem.ReportClock() - start

### Start
log("===== Testing PACEtomo beam centering =====")
log(f"Version {versionPB} - mode: {mode}, method: {method}")

sem.ReportLowDose()                                                     # fills reportedValue2 with the area number
ldReport = sem.ReportLowDose()
ldOn = int(firstValue(ldReport))
ldArea = ldReport[1] if isinstance(ldReport, (tuple, list)) and len(ldReport) > 1 else "?"

checkValves()
xStart, yStart = reportState()

if mode == "monitor":
    log(f"NOTE: Monitoring the beam shift for {monitorMinutes} min, every {monitorInterval} min (no images, no beam movement)...")
    startTime = sem.ReportClock()
    samples = 0
    while sem.ReportClock() - startTime < monitorMinutes * 60:
        x, y = reportShift()
        elapsed = (sem.ReportClock() - startTime) / 60
        samples += 1
        log(f"[{datetime.now().strftime('%H:%M')} | {round(elapsed, 1)} min] x = {round(x, 3)} um | y = {round(y, 3)} um | "
            f"drift: dx = {round(x - xStart, 3)} um | dy = {round(y - yStart, 3)} um")
        if sem.ReportClock() - startTime + monitorInterval * 60 <= monitorMinutes * 60:
            sem.Delay(monitorInterval * 60, "s")
        else:
            sem.Delay(max(0, monitorMinutes * 60 - (sem.ReportClock() - startTime)), "s")
    x, y = reportShift()
    hours = max((sem.ReportClock() - startTime) / 3600, 1e-6)
    log(f"NOTE: Beam drift over {round(hours, 2)} h ({samples} samples): dx = {round(x - xStart, 3)} um | dy = {round(y - yStart, 3)} um "
        f"({round((x - xStart) / hours, 3)} | {round((y - yStart) / hours, 3)} um/h)")

else:
    if ldOn == 1:
        if useLowDoseView:
            sem.GoToLowDoseArea("V")
            sem.Delay(settleDelay, "s")
            log("NOTE: Switched to the View low dose area.")
        if cameraAreaFull:
            sem.SetCameraArea("V", "F")
            log("NOTE: Set the View camera area to Full (restored at the end).")
    else:
        log("WARNING: Low dose mode is off. The beam is centered with the current settings; make sure the beam is fully visible in the image.")

    log(f"Centering the beam {iterations} time(s) with the '{method}' method (max shift {maxShift} um)...")
    totalTime = 0
    for i in range(1, iterations + 1):
        dx, dy, status, secs = centerBeam()
        totalTime += secs
        moved = (dx * dx + dy * dy) ** 0.5
        log(f"[{i}] Beam moved by dx = {round(dx, 3)} um | dy = {round(dy, 3)} um ({round(moved, 3)} um) in {round(secs, 1)} s - {status}")
        if moved > maxShift:
            log(f"WARNING: The beam moved further than the limit of {maxShift} um! Check the beam and the centering.")
        if i == 1 and moved < 0.01:
            log("NOTE: Nothing moved in the first run: the beam may already be centered, or the routine did not work (see the status).")

    xEnd, yEnd = reportShift()
    log(f"NOTE: Beam shift changed from x = {round(xStart, 3)}, y = {round(yStart, 3)} to x = {round(xEnd, 3)}, y = {round(yEnd, 3)} um "
        f"(dx = {round(xEnd - xStart, 3)} um | dy = {round(yEnd - yStart, 3)} um) in {round(totalTime, 1)} s")
    if restoreShift:
        sem.SetBeamShift(xStart, yStart)
        log("NOTE: Restored the beam shift measured at the start (restoreShift = True).")
    else:
        log("NOTE: Keeping the result of the centering (set restoreShift = True to undo it).")

    if ldOn == 1:
        if cameraAreaFull:
            sem.RestoreCameraSet("V")
            log("NOTE: Restored the View camera area.")
        if returnToLowDoseArea != "":
            sem.GoToLowDoseArea(returnToLowDoseArea)
            log(f"NOTE: Switched back to the {returnToLowDoseArea} low dose area.")

log("===== Finished beam centering test =====")
