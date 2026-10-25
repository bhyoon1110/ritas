"""Shared condition choices for analysis forms and the report editor.

Storage keys intentionally retain users' existing browser-local preferences.
Equipment model is a report condition, not the LIMS equipment code.
"""

import json


def _field(field, label, datalist_id, defaults):
    return dict(field=field, label=label, datalistId=datalist_id, defaults=defaults)


_CATALOGS = {
    "FT-IR": {
        "storageKey": "rist-ftir-report-condition-options-v1",
        "fields": [
            _field("equipmentModel", "장비모델", "ftir-report-equipment-model-options", []),
            _field("analysisType", "Type", "ftir-report-type-options", [
                "ATR method", "Transmission", "Reflection", "Diffuse reflectance",
                "Specular reflectance", "Micro ATR", "KBr pellet",
            ]),
            _field("detector", "Detector", "ftir-report-detector-options", ["DTGS", "MCT", "TGS", "InGaAs", "Photoacoustic detector"]),
            _field("crystal", "Crystal", "ftir-report-crystal-options", ["diamond", "ZnSe", "Ge", "KRS-5", "Si", "AMTIR"]),
            _field("resolution", "Resolution", "ftir-report-resolution-options", ["4 cm-1", "2 cm-1", "8 cm-1", "1 cm-1", "16 cm-1"]),
            _field("scanTime", "Scan time", "ftir-report-scan-options", ["64 scans", "32 scans", "128 scans", "16 scans", "256 scans"]),
            _field("range", "Range", "ftir-report-range-options", ["4000 ~ 400 cm-1", "4000 ~ 650 cm-1", "7800 ~ 350 cm-1", "6000 ~ 400 cm-1", "4000 ~ 700 cm-1"]),
        ],
    },
    "RAMAN": {
        "storageKey": "rist-raman-report-condition-options-v2",
        "fields": [
            _field("excitationWavelength", "Excitation Wavelength", "raman-report-excitation-wavelength-options", ["532 nm", "633 nm", "785 nm", "1064 nm", "514 nm", "488 nm", "780 nm"]),
            _field("laserCurrent", "Laser current", "raman-report-laser-current-options", ["1 mA", "5 mA", "10 mA", "20 mA", "50 mA"]),
            _field("excitationPower", "Excitation Power", "raman-report-excitation-power-options", ["0.1 mW", "0.5 mW", "1 mW", "5 mW", "10 mW"]),
            _field("excitationPowerDensity", "Excitation Power density", "raman-report-power-density-options", ["0.01 mW/um2", "0.05 mW/um2", "0.1 mW/um2", "0.5 mW/um2"]),
            _field("ndFilter", "ND filter", "raman-report-nd-filter-options", ["None", "ND 0.5", "ND 1.0", "ND 2.0", "ND 3.0"]),
            _field("spectrographCenterWavelength", "Spectrograph Center wavelength", "raman-report-center-wavelength-options", ["500 nm", "650 nm", "750 nm", "1000 nm"]),
            _field("grating", "Grating", "raman-report-grating-options", ["300 g/mm", "600 g/mm", "1200 g/mm", "1800 g/mm", "2400 g/mm"]),
            _field("slitWidth", "Slit width", "raman-report-slit-width-options", ["25 um", "50 um", "100 um", "200 um"]),
        ],
    },
}


def condition_options(project):
    return _CATALOGS.get(project, {"storageKey": "", "fields": []})


def inject_condition_options(script, project):
    fields = json.dumps(condition_options(project)["fields"], ensure_ascii=False).replace("<", "\\u003c")
    return script.replace("__REPORT_OPTION_FIELDS__", fields)
