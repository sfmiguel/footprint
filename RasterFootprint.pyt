# -*- coding: utf-8 -*-
# RasterFootprint.pyt
# ArcGIS Pro Python Toolbox
# Tools:
#   1. Raster Footprint (Single Image)
#   2. Raster Footprint (Batch - Image Folder)

import os
import math
import arcpy

# Supported raster extensions (batch tool image discovery)
RASTER_EXTENSIONS = {".tif", ".tiff", ".img", ".jp2", ".jpg",
                     ".jpeg", ".png", ".ecw", ".sid", ".vrt"}


class Toolbox:
    def __init__(self):
        self.label = "Raster Footprint Tools"
        self.alias = "RasterFootprint"
        self.tools = [RasterFootprintSingle, RasterFootprintBatch]


# ---------------------------------------------------------------------------
# Geometry helper
# ---------------------------------------------------------------------------

def _dist_point_to_segment(px, py, ax, ay, bx, by):
    """Shortest distance from point (px,py) to segment (ax,ay)-(bx,by)."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / seg_len_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def close_boundary_gaps(polygon_geom, min_gap_width, cell_size):
    """
    Close concave edge gaps using the convex hull as the reference boundary.

    Because satellite image footprints always have straight sides, the convex
    hull of the polygon is the correct outer boundary.  Any section of the
    original boundary that deviates inward from a hull edge is a NoData bay.

    Algorithm
    ---------
    1. Compute the convex hull -> the straight-sided ideal footprint.
    2. For every original polygon vertex compute its distance to the nearest
       hull edge.  Vertices within `cell_size` of a hull edge are classified as
       "on the straight image edge" (staircase steps are <= cell_size/2 away).
       Vertices farther inside are classified as "in a bay".
    3. Find consecutive groups of in-bay vertices.  For each bay the opening
       chord is the distance between the last on-edge vertex before the bay
       and the first on-edge vertex after it -- exactly the initial and final
       vertex the user described.
    4. Bays whose chord >= min_gap_width are closed with a straight line
       (the bay vertices are removed; the two flanking edge vertices connect
       directly).
    """
    sr = polygon_geom.spatialReference

    # ---- convex hull (straight-sided image boundary) -----------------------
    hull      = polygon_geom.convexHull()
    hp        = hull.getPart(0)
    hull_pts  = [(hp.getObject(i).X, hp.getObject(i).Y)
                 for i in range(hp.count) if hp.getObject(i) is not None]
    if hull_pts and hull_pts[0] == hull_pts[-1]:
        hull_pts = hull_pts[:-1]
    hn = len(hull_pts)

    arcpy.AddMessage(
        f"[DEBUG] Convex hull: {hn} vertices.  "
        f"On-hull tolerance = {cell_size:.4f} (1 cell)."
    )

    # ---- outer ring of original polygon ------------------------------------
    part = polygon_geom.getPart(0)
    pts  = [(part.getObject(i).X, part.getObject(i).Y)
            for i in range(part.count) if part.getObject(i) is not None]
    if pts and pts[0] == pts[-1]:
        pts = pts[:-1]
    n = len(pts)

    if n < 3:
        return polygon_geom

    # ---- classify every vertex: on-hull or in-bay --------------------------
    def dist_to_hull(px, py):
        return min(
            _dist_point_to_segment(
                px, py,
                hull_pts[j][0], hull_pts[j][1],
                hull_pts[(j + 1) % hn][0], hull_pts[(j + 1) % hn][1]
            )
            for j in range(hn)
        )

    on_hull = [dist_to_hull(x, y) <= cell_size for (x, y) in pts]
    arcpy.AddMessage(
        f"[DEBUG] Polygon: {n} vertices — "
        f"on-hull: {sum(on_hull)}, in-bay: {n - sum(on_hull)}"
    )

    # rotate so index 0 is on-hull (avoids bays wrapping across the array end)
    first_on = next((i for i in range(n) if on_hull[i]), None)
    if first_on is None:
        arcpy.AddWarning("[WARN] No vertices on hull boundary — returning original.")
        return polygon_geom
    if first_on != 0:
        pts     = pts[first_on:]     + pts[:first_on]
        on_hull = on_hull[first_on:] + on_hull[:first_on]

    # ---- detect bays and close those within max_gap_width ------------------
    skip = set()
    bays_found = bays_closed = 0
    i = 0
    while i < n:
        if on_hull[i]:
            i += 1
            continue

        bay_start = i
        bay_end   = i
        while bay_end + 1 < n and not on_hull[bay_end + 1]:
            bay_end += 1

        # initial and final vertex flanking the bay
        a = pts[bay_start - 1]
        b = pts[(bay_end + 1) % n]
        chord = math.hypot(b[0] - a[0], b[1] - a[1])
        bays_found += 1

        status = "CLOSE" if chord >= min_gap_width else "SKIP"
        arcpy.AddMessage(
            f"[DEBUG] Bay [{bay_start}:{bay_end}] "
            f"({bay_end - bay_start + 1} vertices), chord={chord:.2f} ({status})"
        )

        if chord >= min_gap_width:
            for k in range(bay_start, bay_end + 1):
                skip.add(k)
            bays_closed += 1

        i = bay_end + 1

    arcpy.AddMessage(
        f"[DEBUG] Bays found: {bays_found}, closed: {bays_closed}, "
        f"vertices removed: {len(skip)}"
    )

    new_pts = [arcpy.Point(x, y) for idx, (x, y) in enumerate(pts) if idx not in skip]
    if len(new_pts) < 3:
        return polygon_geom
    new_pts.append(new_pts[0])
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
            displayName   = "Minimum Gap Width to Close (map units)",
            name          = "min_gap_width",
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
                "Please specify the minimum gap width to close."
            )

    # -- Execute --------------------------------------------------------------

    def execute(self, parameters, messages):
        image_path    = parameters[0].valueAsText
        output_shp    = parameters[1].valueAsText
        user_nodata   = parameters[2].value
        close_gaps    = bool(parameters[3].value)
        min_gap_width = parameters[4].value

        arcpy.AddMessage("=== Parameters received ===")
        arcpy.AddMessage(f"  Image path    : {image_path}")
        arcpy.AddMessage(f"  Output shp    : {output_shp}")
        arcpy.AddMessage(f"  User NoData   : {user_nodata}")
        arcpy.AddMessage(f"  Close gaps    : {close_gaps}")
        arcpy.AddMessage(f"  Min gap width : {min_gap_width}")
        arcpy.AddMessage("===========================")

        if close_gaps and min_gap_width is None:
            arcpy.AddError(
                "[ERROR] 'Minimum Gap Width' is required when 'Close Gaps' is enabled."
            )
            return

        if min_gap_width is not None:
            min_gap_width = float(min_gap_width)

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

        # -- Step 3: Close edge gaps with convex-hull vertex approach ---------
        # The convex hull defines the straight-sided ideal footprint boundary.
        # Vertices within one cell size of a hull edge are "on the straight
        # image side"; consecutive vertices further inside form a NoData bay.
        # Each bay's initial and final vertices (the points where it diverges
        # from and returns to the straight image edge) are connected directly
        # if their chord distance is <= max_gap_width.
        arcpy.AddMessage(
            f"[INFO] Closing edge gaps wider than {min_gap_width} map units "
            "by connecting gap endpoints on the straight image sides..."
        )

        try:
            cell_size = float(arcpy.Describe(image_path).children[0].meanCellWidth)
        except Exception:
            try:
                cell_size = float(arcpy.Raster(image_path).meanCellWidth)
            except Exception:
                cell_size = 0.0

        if cell_size <= 0:
            arcpy.AddWarning(
                "[WARN] Could not determine pixel cell size. "
                "Using fallback tolerance of 30 map units."
            )
            cell_size = 30.0

        arcpy.AddMessage(f"[INFO] Raster cell size: {cell_size:.4f}")

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
                    new_geom = close_boundary_gaps(geom, min_gap_width, cell_size)
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


# ---------------------------------------------------------------------------
# Tool: Raster Footprint - Batch (Image Folder)
# ---------------------------------------------------------------------------

class RasterFootprintBatch:

    def __init__(self):
        self.label = "Raster Footprint (Batch - Image Folder)"
        self.description = (
            "Creates polygon footprints for all raster images found in an input "
            "folder. Results are stored in a File GDB feature class with ID and "
            "NamIMG attributes. Supports the same NoData handling and gap-closing "
            "options as the single-image tool."
        )
        self.canRunInBackground = False

    # -- Parameters -----------------------------------------------------------

    def getParameterInfo(self):
        p0 = arcpy.Parameter(
            displayName   = "Input Image Folder",
            name          = "image_folder",
            datatype      = "DEFolder",
            parameterType = "Required",
            direction     = "Input"
        )

        p1 = arcpy.Parameter(
            displayName   = "Output File GDB",
            name          = "out_gdb",
            datatype      = "DEWorkspace",
            parameterType = "Required",
            direction     = "Output"
        )
        p1.filter.list = ["Local Database"]

        p2 = arcpy.Parameter(
            displayName   = "Output Feature Class Name",
            name          = "out_fc_name",
            datatype      = "GPString",
            parameterType = "Required",
            direction     = "Input"
        )
        p2.value = "Footprints"

        p3 = arcpy.Parameter(
            displayName   = "NoData Value (if not defined in rasters)",
            name          = "nodata_value",
            datatype      = "GPDouble",
            parameterType = "Optional",
            direction     = "Input"
        )
        p3.value = None

        p4 = arcpy.Parameter(
            displayName   = "Close Interior Holes and Edge Gaps",
            name          = "close_gaps",
            datatype      = "GPBoolean",
            parameterType = "Optional",
            direction     = "Input"
        )
        p4.value = False

        p5 = arcpy.Parameter(
            displayName   = "Minimum Gap Width to Close (map units)",
            name          = "min_gap_width",
            datatype      = "GPDouble",
            parameterType = "Optional",
            direction     = "Input"
        )
        p5.value   = None
        p5.enabled = False

        return [p0, p1, p2, p3, p4, p5]

    # -- Dynamic UI -----------------------------------------------------------

    def updateParameters(self, parameters):
        parameters[5].enabled = bool(parameters[4].value)

    def isLicensed(self):
        return arcpy.CheckExtension("3D") == "Available"

    def updateMessages(self, parameters):
        if parameters[0].value:
            folder = str(parameters[0].value)
            if os.path.isdir(folder):
                images = [
                    f for f in os.listdir(folder)
                    if os.path.splitext(f)[1].lower() in RASTER_EXTENSIONS
                ]
                if not images:
                    parameters[0].setWarningMessage(
                        "No supported raster files found in this folder. "
                        "Supported formats: "
                        + ", ".join(sorted(RASTER_EXTENSIONS))
                    )
                else:
                    parameters[0].clearMessage()
        if parameters[4].value and not parameters[5].value:
            parameters[5].setWarningMessage(
                "Please specify the minimum gap width to close."
            )

    # -- Execute --------------------------------------------------------------

    def execute(self, parameters, messages):
        image_folder  = parameters[0].valueAsText
        out_gdb       = parameters[1].valueAsText
        out_fc_name   = parameters[2].valueAsText
        user_nodata   = parameters[3].value
        close_gaps    = bool(parameters[4].value)
        min_gap_width = parameters[5].value

        if close_gaps and min_gap_width is None:
            arcpy.AddError(
                "[ERROR] 'Minimum Gap Width' is required when 'Close Gaps' is enabled."
            )
            return

        if min_gap_width is not None:
            min_gap_width = float(min_gap_width)

        arcpy.env.overwriteOutput = True

        # ── Discover and validate images ──────────────────────────────────────
        image_files = sorted([
            f for f in os.listdir(image_folder)
            if os.path.splitext(f)[1].lower() in RASTER_EXTENSIONS
        ])

        if not image_files:
            arcpy.AddError(
                f"[ERROR] No supported raster files found in: {image_folder}"
            )
            return

        arcpy.AddMessage(f"[INFO] Found {len(image_files)} image(s) to process.")
        for f in image_files:
            arcpy.AddMessage(f"         {f}")

        # ── Create GDB if needed ──────────────────────────────────────────────
        gdb_dir  = os.path.dirname(out_gdb)
        gdb_name = os.path.basename(out_gdb)
        if not arcpy.Exists(out_gdb):
            arcpy.management.CreateFileGDB(gdb_dir, gdb_name)
            arcpy.AddMessage(f"[INFO] Created GDB: {out_gdb}")
        else:
            arcpy.AddMessage(f"[INFO] GDB already exists: {out_gdb}")

        arcpy.env.workspace        = out_gdb
        arcpy.env.scratchWorkspace = out_gdb

        # ── Process each image ────────────────────────────────────────────────
        arcpy.CheckOutExtension("3D")
        output_fc = None
        seq_id    = 1
        processed = 0

        for idx, img_file in enumerate(image_files, start=1):
            raster_path = os.path.join(image_folder, img_file)
            arcpy.AddMessage(f"\n[{idx}/{len(image_files)}] {img_file}")

            # ── NoData ────────────────────────────────────────────────────────
            existing_nodata = RasterFootprintSingle._get_nodata(raster_path)
            temp_raster = None

            if existing_nodata is not None:
                raster_to_process = raster_path
            elif user_nodata is not None:
                temp_raster = f"in_memory\\temp_{idx}"
                arcpy.management.CopyRaster(
                    in_raster         = raster_path,
                    out_rasterdataset = temp_raster,
                    nodata_value      = str(user_nodata)
                )
                raster_to_process = temp_raster
            else:
                arcpy.AddWarning(
                    f"  [WARN] No NoData defined — footprint covers full extent."
                )
                raster_to_process = raster_path

            # ── Raster Domain ─────────────────────────────────────────────────
            safe_name  = arcpy.ValidateTableName(
                os.path.splitext(img_file)[0], out_gdb
            )
            tmp_domain = f"tmp_dom_{safe_name}"

            try:
                arcpy.ddd.RasterDomain(
                    in_raster         = raster_to_process,
                    out_feature_class = tmp_domain,
                    out_geometry_type = "POLYGON"
                )
            except Exception as e:
                arcpy.AddWarning(f"  [WARN] RasterDomain failed: {e}")
                if temp_raster and arcpy.Exists(temp_raster):
                    arcpy.management.Delete(temp_raster)
                continue

            if temp_raster and arcpy.Exists(temp_raster):
                arcpy.management.Delete(temp_raster)

            # ── Eliminate holes + close gaps (optional) ───────────────────────
            if close_gaps:
                tmp_no_holes = f"tmp_nh_{safe_name}"
                arcpy.management.EliminatePolygonPart(
                    in_features       = tmp_domain,
                    out_feature_class = tmp_no_holes,
                    condition         = "PERCENT",
                    part_area         = 0,
                    part_area_percent = 99,
                    part_option       = "CONTAINED_ONLY"
                )
                arcpy.management.Delete(tmp_domain)

                try:
                    cell_size = float(
                        arcpy.Describe(raster_path).children[0].meanCellWidth
                    )
                except Exception:
                    try:
                        cell_size = float(arcpy.Raster(raster_path).meanCellWidth)
                    except Exception:
                        cell_size = 30.0

                with arcpy.da.UpdateCursor(tmp_no_holes, ["SHAPE@"]) as cur:
                    for row in cur:
                        if row[0] is None:
                            continue
                        try:
                            cur.updateRow([
                                close_boundary_gaps(
                                    row[0], min_gap_width, cell_size
                                )
                            ])
                        except Exception as e:
                            arcpy.AddWarning(f"  [WARN] Gap closing failed: {e}")

                source_fc = tmp_no_holes
            else:
                source_fc = tmp_domain

            # ── Create output FC on first successful result ───────────────────
            if output_fc is None:
                sr      = arcpy.Describe(raster_path).spatialReference
                fc_path = os.path.join(out_gdb, out_fc_name)
                if arcpy.Exists(fc_path):
                    arcpy.management.Delete(fc_path)
                arcpy.management.CreateFeatureclass(
                    out_path          = out_gdb,
                    out_name          = out_fc_name,
                    geometry_type     = "POLYGON",
                    spatial_reference = sr
                )
                arcpy.management.AddField(
                    fc_path, "ID", "LONG", field_alias="ID"
                )
                arcpy.management.AddField(
                    fc_path, "NamIMG", "TEXT",
                    field_length=255, field_alias="Image Name"
                )
                arcpy.AddMessage(
                    f"[INFO] Output feature class created: {fc_path}"
                )
                output_fc = fc_path

            # ── Append footprint(s) to output FC ──────────────────────────────
            n = 0
            with arcpy.da.SearchCursor(source_fc, ["SHAPE@"]) as src, \
                 arcpy.da.InsertCursor(
                     output_fc, ["SHAPE@", "ID", "NamIMG"]
                 ) as ins:
                for row in src:
                    ins.insertRow((row[0], seq_id, img_file))
                    seq_id += 1
                    n      += 1

            arcpy.management.Delete(source_fc)
            processed += 1
            arcpy.AddMessage(f"  → {n} footprint polygon(s) added.")

        arcpy.CheckInExtension("3D")

        if processed == 0:
            arcpy.AddWarning("[WARN] No footprints were created. Check errors above.")
        else:
            arcpy.AddMessage(f"\n[DONE] {processed}/{len(image_files)} image(s) processed.")
            arcpy.AddMessage(f"       Output: {os.path.join(out_gdb, out_fc_name)}")
