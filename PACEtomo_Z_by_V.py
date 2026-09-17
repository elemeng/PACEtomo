#!Python
# ===================================================================
#ScriptName     PACEtomo_Z_by_V
# Purpose:      Adjusts the stage Z height (fine eucentric Z correction) using the beam-tilt
#               autofocus defocus measurement, as a standalone Python version of the z_by_v.txt
#               SerialEM script. Two steps: coarse in the View low-dose area using its STORED
#               parameters (mag, beam, defocus, intensity), then a fine step in the same area
#               (default) or in the Record low-dose area (if fineMag != 0).
#               More information at http://github.com/eisfabian/PACEtomo
# Created:      2026/09/16
# Revision:     v1.0
# Last Change:  2026/09/17: removed the coarseMag setting and the failure-path SkipAcquiringGroup - the coarse step uses the stored parameters of the View low-dose area; on failure the script restores the stage position and ends in the View low-dose area
#               2026/09/16: initial version
# ===================================================================

############ SETTINGS ############ 

errZ_coarse    = 3          # coarse tolerance [um]
errZ_fine      = 0.5        # fine tolerance [um]
fineMag        = 0          # 0 = fine step in the View low-dose area (G -1 2), using the same stored parameters as the coarse step
                            # 1 (or any non-zero) = fine step in the Record area (G -1 -1), using the high mag
                            # from the low-dose Record configuration - the mag is NOT set in the script
beamTilt       = 5         # beam tilt amplitude [% of full scale] used by the autofocus defocus measurement;
                            # applied to the SerialEM user setting 'AutofocusBeamTilt' for the duration of the script.
                            # Larger tilt gives more image shift per um defocus -> quicker and more robust convergence
                            # (tested on JEOL: 5% clearly better than 0.5%, 10% better still; keep <=10% to stay linear)
eucentricTargetDefocus = 0  # target defocus [um] the Z correction converges to (0 = eucentric focus)
backlashZ      = 0.5        # stage Z backlash [um] used for the relax moves before each correction
intensity_threshold = 50    # minimum mean counts of the autofocus image; below this the adjustment is aborted
maxIterations  = 10         # maximum Z-correction iterations per step
zLimit         = 100        # safety limit [um]: abort if the requested Z correction exceeds this value

debug          = False      # Enables additional output for a few processes

########## END SETTINGS ########## 

versionZbyV = "1.0"

import serialem as sem
import os

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
    #if sem.IsVersionAtLeast("40200", "20240205"):
        #sem.SetNextLogOutputStyle(style, color)
    sem.EchoBreakLines(text)

def eucentricZStep(errZ, area):
    """One Z-correction loop: measure defocus in the given low-dose area (sense for the G command,
    e.g. 2 for View or -1 for Record) and move the stage Z by the defocus error until it is within
    errZ of the target defocus. Returns True if the loop converged."""

    for i in range(maxIterations):
        sem.G(-1, area)                                                                          # measure defocus without changing, in the given area
        report = sem.ReportAutoFocus()                                                           # (defocus [um], failure code) or just (defocus,)
        defocus = report[0]
        afError = report[1] if len(report) > 1 else 0
        meanCounts = sem.ReportMeanCounts()
        if afError != 0:
            log(f"WARNING: Autofocus failed (code {afError}) during eucentric Z refinement. Aborting adjustment!")
            return False
        if meanCounts < intensity_threshold:
            log(f"WARNING: Autofocus image too dark ({meanCounts} counts < {intensity_threshold}). Aborting adjustment!")
            return False
        neededZ = defocus - eucentricTargetDefocus
        log(f"Iteration {i + 1}: defocus {round(defocus, 2)} um, target {round(eucentricTargetDefocus, 2)} um, correction {round(neededZ, 2)} um")
        if abs(neededZ) < errZ:                                                                  # converged
            return True
        if abs(neededZ) > zLimit:
            log(f"WARNING: Requested Z correction {neededZ} um exceeds the safety limit ({zLimit} um). Aborting adjustment!")
            return False
        relax_z1 = neededZ / abs(neededZ) * backlashZ                                            # backlash compensation for direction change
        relax_z2 = -relax_z1
        sem.MoveStage(0, 0, relax_z1)
        sem.MoveStage(0, 0, -neededZ)
        sem.MoveStage(0, 0, relax_z2)
    log("WARNING: Eucentric Z refinement did not converge within the maximum number of iterations!")
    return False

log("===== Running PACEtomo_Z_byV =====")

# Go to the View low-dose area if low dose is on
lowDoseReport = sem.ReportLowDose()
if lowDoseReport[0] == 1:
    sem.GoToLowDoseArea("V")

# Save original stage position so it can be restored on failure
origX, origY, origZ = sem.ReportStageXYZ()

# Set the beam tilt amplitude used by the autofocus defocus measurement (`SetUserSetting`
# restores the previous value automatically when the script stops)
sem.SetUserSetting("AutofocusBeamTilt", beamTilt)

# Standard (eucentric) focus baseline so the measured defocus is comparable to the target
# (`SaveFocus` restores the previous focus on script end)
# sem.SaveFocus() # disabled: Script should end in the View low dose state, do not restore the original focus
# sem.SetEucentricFocus(1) # If this is on, it seems to be a bug in SerialEM: encentric focus is loop to near zero defocus, but record area is tens of um off. 

# Step 1/2: coarse Z correction in the View low-dose area, using the STORED parameters of that area
# (mag, beam, defocus, intensity): the autofocus re-applies the area's settings, so the mag is NOT
# set in this script. Configure the View low-dose area to the desired coarse magnification.
converged = True
magView, *_ = sem.ReportMag()
log(f"Step 1/2: coarse Z adjustment in the View low-dose area (using the stored View parameters, mag {magView}x)...")
if not eucentricZStep(errZ_coarse, 2):
    converged = False

# Step 2/2: fine Z correction, at the same mag (View area) or in the Record area
if converged:
    if fineMag != 0:
        log("Step 2/2: fine Z adjustment in the Record area...")
        converged = eucentricZStep(errZ_fine, -1)
    else:
        log(f"Step 2/2: fine Z adjustment in the View low-dose area (mag {magView}x)...")
        converged = eucentricZStep(errZ_fine, 2)

# On failure: restore the original stage position and leave the scope in the View low-dose area
# (like the success path); the AutofocusBeamTilt user setting is restored automatically on exit
if not converged:
    log("ERROR: Failed to adjust eucentric height. Restoring original stage position!")
    sem.MoveStageTo(origX, origY, origZ)
    if lowDoseReport[0] == 1:
        sem.GoToLowDoseArea("V")                                                                 # end in the View low-dose area, same as the success path
    sem.Exit()

# Leave the scope in the View low-dose area: its mag, defocus offset and intensity are
# applied from the low-dose definition (instead of restoring the original mag/focus,
# which may not match the low-dose configuration)
if lowDoseReport[0] == 1:
    sem.GoToLowDoseArea("V")
log("===== Finished Z_byV =====")