# -*- coding: utf-8 -*-
# RasterFootprint.pyt
# ArcGIS Pro Python Toolbox
# Tools:
#   1. Raster Footprint (Single Image)

import os
import arcpy


class Toolbox:
    def __init__(self):
        self.label = "Raster Footprint Tools"
        self.alias = "RasterFootprint"
        self.tools = [RasterFootprintSingle]


# ---------------------------------------------------------------------------
# Tool: Raster Footprint – Single Image
# ---------------------------------------------------------------------------

class RasterFootprintSingle:

    def __init__(self):
        self.label = "Raster Footprint (Single Image)"
        self.description = (
            "Creates a polygon footprint for a single raster image using the "
            "ArcGIS 3D Analyst 'Raster Domain' tool. NoData pixels are excluded "
            "from the footprint. If the image has no NoData value defined, the "
            "user must supply one."
        )
        self.canRunInBackground = False

    # ── Parameters ────────────────────────────────────────────────────────────

    def getParameterInfo(self):
        # Parameter 0 – Input raster
        p0 = arcpy.Parameter(
            displayName   = "Input Raster",
            name          = "in_raster",
            datatype      = "DERasterDataset",
            parameterType = "Required",
            direction     = "Input"
        )

        # Parameter 1 – Output shapefile
        p1 = arcpy.Parameter(
            displayName   = "Output Footprint Shapefile",
            name          = "out_shp",
            datatype      = "DEShapefile",
            parameterType = "Required",
            direction     = "Output"
        )

        # Parameter 2 – NoData value (optional)
        p2 = arcpy.Parameter(
            displayName   = "NoData Value (if not defined in raster)",
            name          = "nodata_value",
            datatype      = "GPDouble",
            parameterType = "Optional",
            direction     = "Input"
        )
        p2.value = None

        return [p0, p1, p2]

    def isLicensed(self):
        """Allow the tool to run only if 3D Analyst is available."""
        return arcpy.CheckExtension("3D") == "Available"

    def updateMessages(self, parameters):
        """Warn if raster has no NoData and user has not supplied one."""
        if parameters[0].value and not parameters[0].hasError():
            raster_path = str(parameters[0].value)
            if arcpy.Exists(raster_path):
                nodata = self._get_nodata(raster_path)
                if nodata is None and not parameters[2].value:
                    parameters[2].setWarningMessage(
                        "The raster has no NoData value defined. "
                        "The footprint will cover the full raster extent "
                        "unless you provide a NoData value here."
                    )

    def execute(self, parameters, messages):
        image_path   = parameters[0].valueAsText
        output_shp   = parameters[1].valueAsText
        user_nodata  = parameters[2].value  # float or None

        arcpy.env.overwriteOutput = True

        # ── Check / set NoData ─────────────────────────────────────────────
        existing_nodata = self._get_nodata(image_path)
        temp_raster = None

        if existing_nodata is not None:
            arcpy.AddMessage(f"[INFO] NoData value from raster: {existing_nodata}")
            raster_to_process = image_path

        elif user_nodata is not None:
            arcpy.AddMessage(
                f"[INFO] NoData not defined in raster. "
                f"Applying user-provided value: {user_nodata}"
            )
            temp_raster = r"in_memory\temp_raster"
            arcpy.management.CopyRaster(
                in_raster         = image_path,
                out_rasterdataset = temp_raster,
                nodata_value      = str(user_nodata)
            )
            raster_to_process = temp_raster
            arcpy.AddMessage(
                f"[INFO] Temporary in-memory raster created with NoData = {user_nodata}"
            )

        else:
            arcpy.AddWarning(
                "[WARN] No NoData value found and none provided. "
                "Footprint will cover the full raster extent."
            )
            raster_to_process = image_path

        # ── Ensure output directory exists ─────────────────────────────────
        out_dir = os.path.dirname(output_shp)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)

        # ── Run Raster Domain (3D Analyst) ─────────────────────────────────
        arcpy.CheckOutExtension("3D")
        arcpy.AddMessage("[INFO] Running Raster Domain...")

        arcpy.ddd.RasterDomain(
            in_raster         = raster_to_process,
            out_feature_class = output_shp,
            out_geometry_type = "POLYGON"
        )
        arcpy.AddMessage(f"[INFO] Footprint created: {output_shp}")

        # ── Clean up ───────────────────────────────────────────────────────
        if temp_raster and arcpy.Exists(temp_raster):
            arcpy.management.Delete(temp_raster)

        arcpy.CheckInExtension("3D")
        arcpy.AddMessage("[DONE] Process complete.")

    # ── Internal helper ───────────────────────────────────────────────────────

    @staticmethod
    def _get_nodata(raster_path: str):
        """Return the NoData value of the first band, or None if not defined."""
        try:
            band_desc = arcpy.Describe(os.path.join(raster_path, "Band_1"))
            if band_desc.noDataValue is not None:
                return band_desc.noDataValue
        except Exception:
            pass
        try:
            desc = arcpy.Describe(raster_path)
            if desc.noDataValue is not None:
                return desc.noDataValue
        except Exception:
            pass
        return None
