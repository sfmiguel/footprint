# -*- coding: utf-8 -*-
# RasterFootprint.pyt
# ArcGIS Pro Python Toolbox
# Tools:
#   1. Raster Footprint (Single Image)

import os
import math
import arcpy


class Toolbox:
    def __init__(self):
        self.label = "Raster Footprint Tools"
        self.alias = "RasterFootprint"
        self.tools = [RasterFootprintSingle]


# ---------------------------------------------------------------------------
# Geometry helpers for vertex-chain gap closing
# ---------------------------------------------------------------------------

def _signed_area(pts):
    """Shoelace formula. Positive = CCW winding."""
    n = len(pts)
    return sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    ) / 2.0


def _is_reflex(pts, i, ccw):
    """
    True if vertex i is a reflex vertex (interior angle > 180 degrees).
    For a CCW polygon a right-hand turn (negative cross product) is reflex.
    """
    n    = len(pts)
    prev = pts[(i - 1) % n]
    curr = pts[i]
    nxt  = pts[(i + 1) % n]
    cross = (curr[0] - prev[0]) * (nxt[1] - curr[1]) \
          - (curr[1] - prev[1]) * (nxt[0] - curr[0])
    return cross < 0 if ccw else cross > 0


def close_boundary_gaps(polygon_geom, max_gap_width):
    """
    Returns a new arcpy.Polygon where concave edge indentations whose chord
    (straight-line distance between the two boundary points flanking the
    indentation) is <= max_gap_width are replaced by that straight chord.

    The polygon must already have been simplified to remove pixel-grid
    staircase artifacts before calling this (see Step 2b in execute).

    Algorithm
    ---------
    1. Extract outer ring vertices.
    2. Remove the duplicate closing vertex if present.
    3. Rotate the list so index 0 is a non-reflex vertex.
    4. Find maximal consecutive chains of reflex vertices.
    5. For each chain whose chord <= max_gap_width, remove those vertices
       (the two flanking vertices are then connected directly).
    6. Rebuild the polygon from the remaining vertices.
    """
    sr   = polygon_geom.spatialReference
    part = polygon_geom.getPart(0)

    # Extract only the outer ring - stop at first None (ring separator)
    pts = []
    for i in range(part.count):
        pnt = part.getObject(i)
        if pnt is None:
            break
        pts.append((pnt.X, pnt.Y))

    # Remove duplicate closing vertex if present
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]

    n = len(pts)
    if n < 3:
        return polygon_geom

    area = _signed_area(pts)
    ccw  = area > 0

    reflex   = [_is_reflex(pts, i, ccw) for i in range(n)]
    n_reflex = sum(reflex)
    arcpy.AddMessage(f"[DEBUG] Vertices: {n}, reflex: {n_reflex}, winding: {'CCW' if ccw else 'CW'}")

    if n_reflex == 0:
        return polygon_geom

    # Rotate so index 0 is non-reflex (avoids wrap-around chains)
    first_convex = next((i for i in range(n) if not reflex[i]), None)
    if first_convex is None:
        return polygon_geom
    if first_convex != 0:
        pts    = pts[first_convex:]    + pts[:first_convex]
        reflex = reflex[first_convex:] + reflex[:first_convex]

    # Find consecutive chains of reflex vertices and close those within threshold
    skip           = set()
    chains_found   = 0
    chains_closed  = 0
    i = 0
    while i < n:
        if not reflex[i]:
            i += 1
            continue

        chain_start = i
        chain_end   = i
        while chain_end + 1 < n and reflex[chain_end + 1]:
            chain_end += 1

        ax, ay = pts[chain_start - 1]
        bx, by = pts[(chain_end + 1) % n]
        chord  = math.hypot(bx - ax, by - ay)
        chains_found += 1

        status = "CLOSE" if chord <= max_gap_width else "SKIP"
        arcpy.AddMessage(
            f"[DEBUG] Chain [{chain_start}:{chain_end}] "
            f"({chain_end - chain_start + 1} vertices), chord={chord:.2f} ({status})"
        )

        if chord <= max_gap_width:
            for k in range(chain_start, chain_end + 1):
                skip.add(k)
            chains_closed += 1

        i = chain_end + 1

    arcpy.AddMessage(
        f"[DEBUG] Chains found: {chains_found}, closed: {chains_closed}, "
        f"vertices removed: {len(skip)}"
    )

    new_pts = [arcpy.Point(x, y)
               for idx, (x, y) in enumerate(pts)
               if idx not in skip]

    if len(new_pts) < 3:
        return polygon_geom

    new_pts.append(new_pts[0])   # close the ring
    return arcpy.Polygon(arcpy.Array(new_pts), sr)


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
            "optionally be closed by connecting their boundary endpoints with "
            "straight lines."
        )
        self.canRunInBackground = False

    # -- Parameters -----------------------------------------------------------

    def getParameterInfo(self):
        p0 = arcpy.Parameter(
            displayName   = "Input Raster",
            name          = "in_raster",
            datatype      = "DERasterDataset",
            parameterType = "Required",
            direction     = "Input"
        )

        p1 = arcpy.Parameter(
            displayName   = "Output Footprint Shapefile",
            name          = "out_shp",
            datatype      = "DEShapefile",
            parameterType = "Required",
            direction     = "Output"
        )

        p2 = arcpy.Parameter(
            displayName   = "NoData Value (if not defined in raster)",
            name          = "nodata_value",
            datatype      = "GPDouble",
            parameterType = "Optional",
            direction     = "Input"
        )
        p2.value = None

        p3 = arcpy.Parameter(
            displayName   = "Close Interior Holes and Edge Gaps",
            name          = "close_gaps",
            datatype      = "GPBoolean",
            parameterType = "Optional",
            direction     = "Input"
        )
        p3.value = False

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

        # -- Step 2a: Eliminate interior holes --------------------------------
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

        # -- Step 2b: Simplify to remove pixel-grid staircase artifacts -------
        # The raster footprint has a staircase boundary where convex and reflex
        # vertices alternate every pixel. This prevents the chain algorithm from
        # detecting real concave gaps (whose lips also have staircase rims).
        # Generalizing with a tolerance equal to the pixel cell size collapses
        # those single-pixel steps into clean diagonal lines. The actual concave
        # bays (which span many pixels) survive and become proper consecutive
        # reflex-vertex chains that the algorithm can detect and close.
        try:
            cell_size = float(arcpy.Describe(image_path).children[0].meanCellWidth)
        except Exception:
            try:
                cell_size = float(arcpy.Raster(image_path).meanCellWidth)
            except Exception:
                cell_size = 0.0

        tmp_simplified = r"in_memory\tmp_simplified"
        if cell_size > 0:
            arcpy.AddMessage(
                f"[INFO] Simplifying polygon with tolerance = {cell_size:.4f} "
                "(= 1 pixel) to remove staircase artifacts before gap detection..."
            )
            arcpy.management.CopyFeatures(tmp_no_holes, tmp_simplified)
            # Generalize in-place: removes vertices within tolerance of the line
            # between their neighbours. Does not require Cartography extension.
            arcpy.edit.Generalize(tmp_simplified, f"{cell_size} Unknown")
        else:
            arcpy.AddWarning(
                "[WARN] Could not determine pixel cell size; "
                "skipping staircase simplification. "
                "Gap detection may miss raster-edge bays."
            )
            arcpy.management.CopyFeatures(tmp_no_holes, tmp_simplified)

        arcpy.management.Delete(tmp_no_holes)

        # -- Step 3: Close edge gaps with vertex-chain straight-line closing --
        arcpy.AddMessage(
            f"[INFO] Closing edge gaps narrower than {max_gap_width} map units "
            "by connecting boundary endpoints with straight lines..."
        )

        arcpy.management.CopyFeatures(tmp_simplified, output_shp)
        arcpy.management.Delete(tmp_simplified)

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
