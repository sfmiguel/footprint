# -*- coding: utf-8 -*-
# Raster Footprint to Shapefile – single GeoTIFF
# Uses ArcGIS 3D Analyst "Raster Domain" tool
# Footprint excludes NoData pixels
#
# Usage:
#   raster_footprint_single.py <image_path> <output_shp> [nodata_value]
#
# Arguments:
#   image_path   – Full path to the input GeoTIFF (or any ArcGIS-supported raster)
#   output_shp   – Full path to the output shapefile (.shp)
#   nodata_value – (Optional) NoData value to apply if the image has none defined

import os
import sys
import arcpy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_nodata_value(raster_path: str):
    """
    Return the NoData value of the first band, or None if not defined.
    Checks band-level first, then dataset-level.
    """
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


def ensure_output_dir(output_shp: str) -> None:
    """Create the output directory if it does not exist."""
    out_dir = os.path.dirname(output_shp)
    if out_dir and not os.path.isdir(out_dir):
        os.makedirs(out_dir)
        arcpy.AddMessage(f"[INFO] Created output folder: {out_dir}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(image_path: str, output_shp: str, user_nodata=None) -> None:

    # ── Validate input ───────────────────────────────────────────────────────
    if not arcpy.Exists(image_path):
        sys.exit(f"[ERROR] Image not found: {image_path}")

    ensure_output_dir(output_shp)

    # ── Check / set NoData ───────────────────────────────────────────────────
    existing_nodata = get_nodata_value(image_path)
    temp_raster = None

    if existing_nodata is not None:
        arcpy.AddMessage(f"[INFO] NoData value from raster: {existing_nodata}")
        raster_to_process = image_path

    elif user_nodata is not None:
        arcpy.AddMessage(
            f"[INFO] NoData not defined in raster. "
            f"Applying user-provided value: {user_nodata}"
        )
        # Copy to an in-memory raster and define NoData there
        # so the original file is never modified
        temp_raster = r"in_memory\temp_raster"
        arcpy.management.CopyRaster(
            in_raster        = image_path,
            out_rasterdataset= temp_raster,
            nodata_value     = str(user_nodata)
        )
        raster_to_process = temp_raster
        arcpy.AddMessage(f"[INFO] Temporary in-memory raster created with NoData = {user_nodata}")

    else:
        arcpy.AddWarning(
            "[WARN] No NoData value found in the raster and none provided by the user. "
            "The footprint will cover the full raster extent."
        )
        raster_to_process = image_path

    # ── Check out 3D Analyst ─────────────────────────────────────────────────
    if arcpy.CheckExtension("3D") != "Available":
        sys.exit("[ERROR] 3D Analyst extension is not available.")
    arcpy.CheckOutExtension("3D")

    # ── Run Raster Domain ────────────────────────────────────────────────────
    arcpy.AddMessage("[INFO] Running Raster Domain...")
    arcpy.ddd.RasterDomain(
        in_raster        = raster_to_process,
        out_feature_class= output_shp,
        out_geometry_type= "POLYGON"
    )
    arcpy.AddMessage(f"[INFO] Footprint created: {output_shp}")

    # ── Clean up ─────────────────────────────────────────────────────────────
    if temp_raster and arcpy.Exists(temp_raster):
        arcpy.management.Delete(temp_raster)

    arcpy.CheckInExtension("3D")
    arcpy.AddMessage("[DONE] Process complete.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    arcpy.env.overwriteOutput = True

    arcpy.AddMessage("--------------------")
    arcpy.AddMessage("Raster Footprint – Single Image")
    arcpy.AddMessage("--------------------")

    if len(sys.argv) < 3:
        sys.exit(
            "[ERROR] Not enough arguments.\n"
            "Usage: raster_footprint_single.py <image_path> <output_shp> [nodata_value]"
        )

    _image_path  = sys.argv[1]
    _output_shp  = sys.argv[2]
    _user_nodata = float(sys.argv[3]) if len(sys.argv) > 3 else None

    main(_image_path, _output_shp, _user_nodata)
