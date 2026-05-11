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
# Geometry helper: close boundary indentations with straight chord lines
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

    Interior holes must already have been eliminated before calling this.
    Works on the outer ring (part 0) of the polygon.

    Algorithm
    ---------
    1. Extract outer ring vertices (stop at first None separator – ArcGIS uses
       None to separate rings within a part; only the outer ring is needed).
    2. Remove the duplicate closing vertex if present.
    3. Rotate the list so index 0 is a non-reflex vertex – prevents chains
       from wrapping across the array boundary.
    4. Find maximal consecutive chains of reflex vertices.
    5. For each chain whose chord <= max_gap_width, mark vertices for removal.
       Removing them leaves an implicit straight edge A -> B.
    6. Rebuild the polygon from the remaining vertices.
    """
    sr   = polygon_geom.spatialReference
    part = polygon_geom.getPart(0)

    # Extract only the outer ring – stop at first None (ring separator)
    pts = []
    for i in range(part.count):
        pnt = part.getObject(i)
        if pnt is None:
            break                          # reached inner-ring separator
        pts.append((pnt.X, pnt.Y))

    # Remove duplicate closing vertex if present
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]

    n = len(pts)
    arcpy.AddMessage(f"[DEBUG] Outer ring has {n} vertices.")

    if n < 3:
        arcpy.AddMessage("[DEBUG] Too few vertices – returning original.")
        return polygon_geom

    area  = _signed_area(pts)
    # ArcGIS exterior rings are typically CW (negative area in math convention).
    # Treat both cases correctly.
    ccw    = area > 0
    arcpy.AddMessage(f"[DEBUG] Ring winding: {'CCW' if ccw else 'CW'}, signed area={area:.2f}")

    reflex = [_is_reflex(pts, i, ccw) for i in range(n)]
    n_reflex = sum(reflex)
    arcpy.AddMessage(f"[DEBUG] Reflex vertices found: {n_reflex}")

    if n_reflex == 0:
        arcpy.AddMessage("[DEBUG] No reflex vertices – no gaps to close.")
        return polygon_geom

    # Rotate so that index 0 is a non-reflex vertex.
    # Guarantees no chain wraps around the array boundary.
    first_convex = next((i for i in range(n) if not reflex[i]), None)
    if first_convex is None:
        arcpy.AddMessage("[DEBUG] All vertices reflex – degenerate polygon.")
        return polygon_geom
    if first_convex != 0:
        pts    = pts[first_convex:]    + pts[:first_convex]
        reflex = reflex[first_convex:] + reflex[:first_convex]

    # Find maximal chains of consecutive reflex vertices and decide which to close
    skip        = set()
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

        a_idx = chain_start - 1
        b_idx = (chain_end  + 1) % n

        ax, ay = pts[a_idx]
        bx, by = pts[b_idx]
        chord  = math.hypot(bx - ax, by - ay)
        chains_found += 1
        arcpy.AddMessage(
            f"[DEBUG] Chain [{chain_start}:{chain_end}] "
            f"({chain_end - chain_start + 1} vertices), chord={chord:.2f} "
            f"({'CLOSE' if chord <= max_gap_width else 'SKIP – too wide'})"
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

    # Rebuild vertex list
    new_pts = [arcpy.Point(x, y)
               for idx, (x, y) in enumerate(pts)
               if idx not in skip]

    if len(new_pts) < 3:
        arcpy.AddMessage("[DEBUG] Too few vertices after closing – returning original.")
        return polygon_geom

    new_pts.append(new_pts[0])   # close the ring
    return arcpy.Polygon(arcpy.Array(new_pts), sr)


# ---------------------------------------------------------------------------
# Tool: Raster Footprint – Single Image
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

        # Parameter 3 – Close gaps toggle
        p3 = arcpy.Parameter(
            displayName   = "Close Interior Holes and Edge Gaps",
            name          = "close_gaps",
            datatype      = "GPBoolean",
            parameterType = "Optional",
            direction     = "Input"
        )
        p3.value = False

        # Parameter 4 – Maximum gap width (active only when close_gaps = True)
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

    # ── Dynamic UI ─────────────────────────────────────────────────────────────

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

    # ── Execute ────────────────────────────────────────────────────────────────

    def execute(self, parameters, messages):
        image_path    = parameters[0].valueAsText
        output_shp    = parameters[1].valueAsText
        user_nodata   = parameters[2].value
        close_gaps    = bool(parameters[3].value)
        max_gap_width = parameters[4].value

        # ── Log received parameters ────────────────────────────────────────
        arcpy.AddMessage("=== Parameters received ===")
        arcpy.AddMessage(f"  Image path    : {image_path}")
        arcpy.AddMessage(f"  Output shp    : {output_shp}")
        arcpy.AddMessage(f"  User NoData   : {user_nodata}")
        arcpy.AddMessage(f"  Close gaps    : {close_gaps}")
        arcpy.AddMessage(f"  Max gap width : {max_gap_width}")
        arcpy.AddMessage("===========================")

        if close_gaps and max_gap_width is None:
            arcpy.AddError("[ERROR] 'Maximum Gap Width' is required when 'Close Gaps' is enabled.")
            return

        if max_gap_width is not None:
            max_gap_width = float(max_gap_width)

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

        # ── Step 1: Raster Domain ──────────────────────────────────────────
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

        # ── Step 2: Eliminate interior holes ──────────────────────────────
        # Uses EliminatePolygonPart with CONTAINED_ONLY to remove only holes
        # that are fully inside the polygon (not connected to the edge).
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

        # ── Step 3: Close edge indentations with straight chord lines ──────
        # For each chain of consecutive reflex (inward-pointing) vertices on
        # the outer ring, if the straight-line distance between the two flanking
        # convex vertices is <= max_gap_width, the chain is removed and those
        # two vertices are connected directly with a straight line.
        # Gaps connected to the outside are handled this way.
        # No rounding – the closing line is always perfectly straight.
        arcpy.AddMessage(
            f"[INFO] Closing edge gaps narrower than {max_gap_width} map units "
            "by connecting boundary endpoints with straight lines..."
        )

        arcpy.management.CopyFeatures(tmp_no_holes, output_shp)
        arcpy.management.Delete(tmp_no_holes)

        with arcpy.da.UpdateCursor(output_shp, ["SHAPE@"]) as cursor:
            for row in cursor:
                geom = row[0]
                if geom is None:
                    continue
                arcpy.AddMessage(f"[INFO] Processing polygon with {geom.pointCount} points across {geom.partCount} part(s)...")
                try:
                    new_geom = close_boundary_gaps(geom, max_gap_width)
                    cursor.updateRow([new_geom])
                except Exception as e:
                    arcpy.AddWarning(f"[WARN] Gap closing failed for a polygon: {e}")
                    import traceback
                    arcpy.AddWarning(traceback.format_exc())

        arcpy.AddMessage(f"[INFO] Footprint created: {output_shp}")
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
