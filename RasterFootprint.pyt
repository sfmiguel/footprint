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
# Geometry helper: close boundary gaps using morphological closing
# ---------------------------------------------------------------------------

def close_boundary_gaps(polygon_geom, max_gap_width):
    """
    Returns a new arcpy.Polygon where edge gaps (concave bays) narrower than
    max_gap_width are closed using morphological closing:
      1. Buffer polygon outward by max_gap_width / 2  -> fills narrow bays
      2. Buffer the result inward by max_gap_width / 2 -> restores outer boundary

    This approach works correctly for raster (pixelated/staircase) polygon
    boundaries where concave and convex vertices alternate and vertex-chain
    methods cannot detect the actual gaps.

    Interior holes must already have been eliminated before calling this.
    """
    radius = max_gap_width / 2.0
    arcpy.AddMessage(
        f"[DEBUG] Morphological closing: gap width = {max_gap_width:.4f}, "
        f"buffer radius = {radius:.4f}"
    )
    arcpy.AddMessage(f"[DEBUG] Original polygon area  : {polygon_geom.area:.4f}")

    closed = polygon_geom.buffer(radius).buffer(-radius)

    if closed is None or closed.area == 0:
        arcpy.AddWarning(
            "[WARN] Morphological closing produced an empty result - "
            "returning original polygon."
        )
        return polygon_geom

    arcpy.AddMessage(f"[DEBUG] Closed polygon area     : {closed.area:.4f}")
    return closed


# ---------------------------------------------------------------------------
# Tool: Raster Footprint - Single Image
# ---------------------------------------------------------------------------

class RasterFootprintSingle:

    def __init__(self):
        self.label = "Raster Footprint (Single Image)"
        self.description = (
            "Creates a polygon footprint for a single raster image using the "
            "ArcGIS 3D Analyst 'Raster Domain' tool. NoData pixels are excluded "
            "from the footprint. Interior NoData holes and edge indentations can "
            "optionally be closed using morphological closing."
        )
        self.canRunInBackground = False

    # -- Parameters -----------------------------------------------------------

    def getParameterInfo(self):
        # Parameter 0 - Input raster
        p0 = arcpy.Parameter(
            displayName   = "Input Raster",
            name          = "in_raster",
            datatype      = "DERasterDataset",
            parameterType = "Required",
            direction     = "Input"
        )

        # Parameter 1 - Output shapefile
        p1 = arcpy.Parameter(
            displayName   = "Output Footprint Shapefile",
            name          = "out_shp",
            datatype      = "DEShapefile",
            parameterType = "Required",
            direction     = "Output"
        )

        # Parameter 2 - NoData value (optional)
        p2 = arcpy.Parameter(
            displayName   = "NoData Value (if not defined in raster)",
            name          = "nodata_value",
            datatype      = "GPDouble",
            parameterType = "Optional",
            direction     = "Input"
        )
        p2.value = None

        # Parameter 3 - Close gaps toggle
        p3 = arcpy.Parameter(
            displayName   = "Close Interior Holes and Edge Gaps",
            name          = "close_gaps",
            datatype      = "GPBoolean",
            parameterType = "Optional",
            direction     = "Input"
        )
        p3.value = False

        # Parameter 4 - Maximum gap width (active only when close_gaps = True)
        p4 = arcpy.Parameter(
            displayName   = "Maximum Gap Width to Close (map units)",
            name          = "max_gap_width",
            datatype      = "GPDouble",
            parameterType = "Optional",
            direction     = "Input"
        )
        p4.value   = None
        p4.enabled = False

        return [p0, p1, p2, p3, p4]

    # -- Dynamic UI -----------------------------------------------------------

    def updateParameters(self, parameters):
        parameters[4].enabled = bool(parameters[3].value)

    def isLicensed(self):
        return arcpy.CheckExtension("3D") == "Available"

    def updateMessages(self, parameters):
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
        if parameters[3].value and not parameters[4].value:
            parameters[4].setWarningMessage(
                "Please specify the maximum gap width to close."
            )

    # -- Execute --------------------------------------------------------------

    def execute(self, parameters, messages):
        image_path    = parameters[0].valueAsText
        output_shp    = parameters[1].valueAsText
        user_nodata   = parameters[2].value
        close_gaps    = bool(parameters[3].value)
        max_gap_width = parameters[4].value

        # -- Log received parameters ------------------------------------------
        arcpy.AddMessage("=== Parameters received ===")
        arcpy.AddMessage(f"  Image path    : {image_path}")
        arcpy.AddMessage(f"  Output shp    : {output_shp}")
        arcpy.AddMessage(f"  User NoData   : {user_nodata}")
        arcpy.AddMessage(f"  Close gaps    : {close_gaps}")
        arcpy.AddMessage(f"  Max gap width : {max_gap_width}")
        arcpy.AddMessage("===========================")

        if close_gaps and max_gap_width is None:
            arcpy.AddError(
                "[ERROR] 'Maximum Gap Width' is required when 'Close Gaps' is enabled."
            )
            return

        if max_gap_width is not None:
            max_gap_width = float(max_gap_width)

        arcpy.env.overwriteOutput = True

        # -- Check / set NoData -----------------------------------------------
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

        # -- Ensure output directory exists -----------------------------------
        out_dir = os.path.dirname(output_shp)
        if out_dir and not os.path.isdir(out_dir):
            os.makedirs(out_dir)

        # -- Step 1: Raster Domain --------------------------------------------
        arcpy.CheckOutExtension("3D")
        arcpy.AddMessage("[INFO] Running Raster Domain...")

        tmp_domain = r"in_memory\tmp_domain" if close_gaps else output_shp

        arcpy.ddd.RasterDomain(
            in_raster         = raster_to_process,
            out_feature_class = tmp_domain,
            out_geometry_type = "POLYGON"
        )

        if temp_raster and arcpy.Exists(temp_raster):
            arcpy.management.Delete(temp_raster)

        arcpy.CheckInExtension("3D")

        if not close_gaps:
            arcpy.AddMessage(f"[INFO] Footprint created: {output_shp}")
            arcpy.AddMessage("[DONE] Process complete.")
            return

        # -- Step 2: Eliminate interior holes ---------------------------------
        arcpy.AddMessage("[INFO] Eliminating interior holes...")
        tmp_no_holes = r"in_memory\tmp_no_holes"

        arcpy.management.EliminatePolygonPart(
            in_features       = tmp_domain,
            out_feature_class = tmp_no_holes,
            condition         = "PERCENT",
            part_area         = 0,
            part_area_percent = 99,
            part_option       = "CONTAINED_ONLY"
        )
        arcpy.management.Delete(tmp_domain)

        # -- Step 3: Close edge gaps using morphological closing --------------
        # Buffer polygon outward by max_gap_width/2 (fills narrow concave bays)
        # then buffer inward by max_gap_width/2 (restores outer boundary).
        # This closes any concave bay whose width is <= max_gap_width,
        # regardless of whether the boundary is smooth or raster-staircase.
        arcpy.AddMessage(
            f"[INFO] Closing edge gaps narrower than {max_gap_width} map units "
            "using morphological closing (buffer out + buffer in)..."
        )

        arcpy.management.CopyFeatures(tmp_no_holes, output_shp)
        arcpy.management.Delete(tmp_no_holes)

        with arcpy.da.UpdateCursor(output_shp, ["SHAPE@"]) as cursor:
            for row in cursor:
                geom = row[0]
                if geom is None:
                    continue
                arcpy.AddMessage(
                    f"[INFO] Processing polygon with {geom.pointCount} points "
                    f"across {geom.partCount} part(s)..."
                )
                try:
                    new_geom = close_boundary_gaps(geom, max_gap_width)
                    cursor.updateRow([new_geom])
                except Exception as e:
                    arcpy.AddWarning(f"[WARN] Gap closing failed for a polygon: {e}")
                    import traceback
                    arcpy.AddWarning(traceback.format_exc())

        arcpy.AddMessage(f"[INFO] Footprint created: {output_shp}")
        arcpy.AddMessage("[DONE] Process complete.")

    # -- Internal helper ------------------------------------------------------

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
