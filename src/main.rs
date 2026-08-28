/*
This file is part of wbt-viewshed-offsets, a WhiteboxTools plugin.

It began as a verbatim copy of viewshed.rs from the WhiteboxTools geospatial
analysis library (https://github.com/jblindsay/whitebox-tools) at commit
3d7c73cf562b6c58d2649c37f570e3e9a90322f2, and retains that file's visibility
algorithm: the eight-facet view-angle sweep.

Original author: Dr. John Lindsay. Created 10/01/2018, last modified 12/10/2018.
License: MIT. See LICENSE and NOTICE.

This plugin adds parameters WhiteboxTools' built-in Viewshed does not expose:

  --offset_b      target/receiver height above ground (GRASS OFFSETB).
  --curvature     earth-curvature + atmospheric-refraction correction,
                  with --refraction_k and --earth_radius.
  --max_dist      maximum viewing distance, masked to a disc.
  --z_factor      vertical-to-horizontal unit conversion, for --curvature.
  --station_z     absolute observer ground elevation, replacing the DEM
                  lookup, for observers not standing on the terrain.

With `--offset_b=0`, no `--curvature` and no `--max_dist`, output is identical
to upstream Viewshed. That invariant is the tool's acceptance test.

There is exactly one deliberate divergence: a station sitting on a NoData cell
is refused rather than silently given `nodata + height` as its eye elevation.
See the marker at the `stn_z` assignment.

Modifications to the vendored algorithm are marked inline with dated
CHANGED/ADDED blocks stating what changed, why, and whether the change can move
a result, with the original lines retained beside each one, commented out.
*/

use whitebox_common::structures::Array2D;
use whitebox_common::utils::get_formatted_elapsed_time;
use whitebox_raster::Raster;
use whitebox_vector::*;
use num_cpus;
use std::env;
use std::f64;
use std::io::{Error, ErrorKind};
use std::path;
use std::sync::mpsc;
use std::sync::Arc;
use std::thread;
use std::time::Instant;

const TOOL_NAME: &str = "ViewshedOffsets";

fn main() {
    let args: Vec<String> = env::args().collect();

    // Deliberate deviation from the upstream plugin template, which indexes
    // args[1] before testing args.len() and so panics when invoked bare.
    if args.len() <= 1 || args[1].trim() == "help" {
        help();
        return;
    }

    if args[1].trim() == "version" {
        version();
        return;
    }

    if args[1].trim() == "run" {
        match run(&args) {
            Ok(_) => {}
            Err(e) => panic!("{:?}", e),
        }
        return;
    }

    help();
}

fn help() {
    let mut ext = "";
    if cfg!(target_os = "windows") {
        ext = ".exe";
    }

    let exe_name = &format!("viewshed_offsets{}", ext);
    let sep: String = path::MAIN_SEPARATOR.to_string();
    let s = r#"
    viewshed_offsets Help

    Identifies the viewshed for a point or set of points, with a target offset
    (OFFSETB), optional earth-curvature and refraction correction, and an
    optional maximum viewing distance.

    The following commands are recognized:
    help       Prints help information.
    run        Runs the tool.
    version    Prints the tool version information.

    The following flags can be used with the 'run' command:
    -d, --dem        Name of the input DEM raster file.
    --stations       Name of the input viewing station vector (points).
    -o, --output     Name of the output raster file.
    --offset_a       Viewing station height above ground, in z units. Default 2.0.
                     Accepted as --height for compatibility with WBT Viewshed.
    --offset_b       Target height above ground, in z units. Default 0.0, which
                     reproduces WBT Viewshed exactly.
    --max_dist       Maximum viewing distance in xy units. Default unlimited.
                     Masks to a disc, not the bounding square.
    --curvature      Apply earth-curvature and atmospheric-refraction correction.
                     Requires --refraction_k.
    --refraction_k   Refraction coefficient for --curvature. No default; a value
                     must be given so that it is a stated parameter rather than
                     an inherited one. 0.13 is the common geodetic value.
    --earth_radius   Earth radius, in the DEM's horizontal units. Default 6371000
                     (meters). Also acts as confirmation when those units are
                     projected but not meters.
    --station_z      Absolute ground elevation for the stations, replacing the DEM
                     lookup. For a boat or aircraft, or where the DEM is NoData
                     under the station. --offset_a is still added on top.
    --z_factor       Multiplier converting the DEM's VERTICAL units to its
                     horizontal units, for --curvature only. Default 1.0. Set to
                     0.3048 for elevations in feet with coordinates in meters.

    Input/output file names can be fully qualified, or can rely on the working
    directory contained in the WhiteboxTools settings.json file.

    Example Usage:
    >> .*EXE_NAME run --dem=DEM.tif --stations=sites.shp -o=vs.tif --offset_a=1.7 --offset_b=1.7 --curvature --refraction_k=0.13 --max_dist=50000

    "#
    .replace("*", &sep)
    .replace("EXE_NAME", exe_name);
    println!("{}", s);
}

fn version() {
    const VERSION: Option<&'static str> = option_env!("CARGO_PKG_VERSION");
    println!(
        "viewshed_offsets v{}. Derived from WhiteboxTools Viewshed by Dr. John B. Lindsay (c) 2018, MIT.",
        VERSION.unwrap_or("Unknown version")
    );
}

/// Parse an f64 argument in either `--flag=value` or `--flag value` form,
/// matching the upstream plugin template's accepted syntaxes.
fn parse_f64(vec: &Vec<&str>, keyval: bool, args: &Vec<String>, i: usize, flag_val: &str) -> f64 {
    if keyval {
        vec[1]
            .to_string()
            .parse::<f64>()
            .expect(&format!("Error parsing {}", flag_val))
    } else {
        args[i + 1]
            .to_string()
            .parse::<f64>()
            .expect(&format!("Error parsing {}", flag_val))
    }
}

fn run(args: &Vec<String>) -> Result<(), Error> {
    let mut input_file = String::new();
    let mut stations_file = String::new();
    let mut output_file = String::new();
    let mut height = 2.0;

    // ADDED 2026-08-15 (Claude Opus 5, operator Daniel; plan "wbt-viewshed-offsets").
    // WHAT: the three parameters upstream Viewshed does not expose -- a target
    //       offset, a curvature/refraction correction, and a distance cap.
    // WHY:  WBT's viewshed applies the viewing height to the station only, so
    //       every target is a 0 m ground-surface receiver, and it corrects for
    //       neither earth curvature nor refraction. Callers currently work around
    //       both by pre-adjusting and pre-clipping the DEM per observer, which
    //       costs a full raster write per station.
    // WHY IT CANNOT MOVE A NUMBER: every default here is inert. offset_b = 0
    //       makes the target angle identical to the ground angle; curvature =
    //       false leaves elevations untouched; max_dist = infinity masks nothing.
    //       An invocation naming none of them therefore takes the same arithmetic
    //       path as upstream. Enforced by tests/parity.py, which requires zero
    //       differing cells against wbt.viewshed() over a real DEM.
    let mut offset_b = 0f64;
    let mut max_dist = f64::INFINITY;
    let mut curvature = false;
    let mut refraction_k: Option<f64> = None;
    let mut earth_radius = 6371000f64;
    // ADDED 2026-08-15 (Claude Opus 5, operator Daniel; generalization pass).
    // WHAT: z_factor, the standard WhiteboxTools multiplier for DEMs whose
    //       vertical and horizontal units differ.
    // WHY:  upstream Viewshed does not need one, because it only ever COMPARES
    //       angles and a consistent unit mismatch cancels. Curvature breaks that
    //       invariant: the drop is a physical length computed in horizontal
    //       units and subtracted from an elevation. On a DEM with elevations in
    //       feet and coordinates in meters the correction would be wrong by a
    //       factor of 3.28 -- with no error and no warning.
    // WHY IT CANNOT MOVE A NUMBER: the default 1.0 makes the conversion an
    //       identity, and it is applied only inside the curvature term, which is
    //       itself inert unless --curvature was given.
    let mut z_factor = 1f64;
    // ADDED 2026-08-15 (Claude Opus 5, operator Daniel; generalization pass).
    // WHAT: an explicit absolute ground elevation for the viewing stations,
    //       replacing the DEM lookup.
    // WHY:  the observer is not always standing on the terrain surface. A boat at
    //       sea, an aircraft, or a station sitting on a DEM void has no usable
    //       elevation beneath it, and on many DEMs the sea is NoData -- in which
    //       case the lookup returns the NoData sentinel and the observer ends up
    //       thousands of meters underground with no error. See the guard below.
    // WHY IT CANNOT MOVE A NUMBER: None leaves the DEM lookup exactly as upstream
    //       wrote it. --offset_a is still added on top, so `--station_z=0
    //       --offset_a=1.7` places an eye 1.7 m above sea level.
    let mut station_z: Option<f64> = None;

    if args.len() == 0 {
        return Err(Error::new(
            ErrorKind::InvalidInput,
            "Tool run with no parameters.",
        ));
    }
    for i in 0..args.len() {
        let mut arg = args[i].replace("\"", "");
        arg = arg.replace("\'", "");
        let cmd = arg.split("="); // in case an equals sign was used
        let vec = cmd.collect::<Vec<&str>>();
        let mut keyval = false;
        if vec.len() > 1 {
            keyval = true;
        }
        let flag_val = vec[0].to_lowercase().replace("--", "-");
        if flag_val == "-i" || flag_val == "-input" || flag_val == "-dem" {
            input_file = if keyval {
                vec[1].to_string()
            } else {
                args[i + 1].to_string()
            };
        } else if flag_val == "-stations" || flag_val == "-station" {
            stations_file = if keyval {
                vec[1].to_string()
            } else {
                args[i + 1].to_string()
            };
        } else if flag_val == "-o" || flag_val == "-output" {
            output_file = if keyval {
                vec[1].to_string()
            } else {
                args[i + 1].to_string()
            };
        // CHANGED 2026-08-15 (Claude Opus 5, operator Daniel; plan "wbt-viewshed-offsets").
        // WHAT: accept --offset_a as an alias for --height.
        // WHY:  the tool now has a symmetric pair of offsets, and naming them
        //       offset_a/offset_b matches GRASS r.viewshed, which is what users
        //       coming to this plugin will already know.
        // WHY IT CANNOT MOVE A NUMBER: --height is still accepted and still binds
        //       the same variable, so existing call sites are unaffected. This
        //       widens the accepted flag set; it removes nothing.
        // ORIGINAL, left in place per the code-edit marking convention:
        //     } else if flag_val == "-height" {
        } else if flag_val == "-height" || flag_val == "-offset_a" {
            height = if keyval {
                vec[1]
                    .to_string()
                    .parse::<f64>()
                    .expect(&format!("Error parsing {}", flag_val))
            } else {
                args[i + 1]
                    .to_string()
                    .parse::<f64>()
                    .expect(&format!("Error parsing {}", flag_val))
            };
        // ADDED 2026-08-15 (Claude Opus 5, operator Daniel; plan "wbt-viewshed-offsets").
        // Parsing for the new parameters. See the ADDED block at their
        // declarations above for why each default is inert.
        } else if flag_val == "-offset_b" {
            offset_b = parse_f64(&vec, keyval, args, i, &flag_val);
        } else if flag_val == "-max_dist" || flag_val == "-maxdist" {
            max_dist = parse_f64(&vec, keyval, args, i, &flag_val);
        } else if flag_val == "-refraction_k" || flag_val == "-refractionk" {
            refraction_k = Some(parse_f64(&vec, keyval, args, i, &flag_val));
        } else if flag_val == "-earth_radius" || flag_val == "-earthradius" {
            earth_radius = parse_f64(&vec, keyval, args, i, &flag_val);
        } else if flag_val == "-zfactor" || flag_val == "-z_factor" {
            z_factor = parse_f64(&vec, keyval, args, i, &flag_val);
        } else if flag_val == "-station_z" || flag_val == "-stationz" {
            station_z = Some(parse_f64(&vec, keyval, args, i, &flag_val));
        } else if flag_val == "-curvature" {
            // Bare `--curvature` means true, matching WBT's convention for
            // boolean flags; `--curvature=false` is also honored.
            curvature = if keyval {
                !vec[1].to_lowercase().contains("false")
            } else {
                true
            };
        }
    }

    // CHANGED 2026-08-15 (Claude Opus 5, operator Daniel; plan "wbt-viewshed-offsets").
    // WHAT: read the configuration before the banner rather than after, take
    //       `verbose` and `working_directory` from it, and name the tool with a
    //       constant instead of the WhiteboxTool trait's get_tool_name().
    // WHY:  a plugin is a standalone binary. It has no trait object to ask for
    //       its name and is not handed a working directory by the caller; the
    //       upstream plugin template reads both from settings.json via
    //       whitebox_common::configs::get_configs(). Reordering is forced -- the
    //       banner's `verbose` test now depends on the configs read.
    // WHY IT CANNOT MOVE A NUMBER: this is I/O plumbing and console output only.
    //       No value used by the visibility computation is read, written or
    //       reordered here; num_procs is derived exactly as before.
    // ORIGINAL, left in place per the code-edit marking convention:
    //     if verbose {
    //         let tool_name = self.get_tool_name();
    //         let welcome_len = format!("* Welcome to {} *", tool_name).len().max(28);
    //         // 28 = length of the 'Powered by' by statement.
    //         println!("{}", "*".repeat(welcome_len));
    //         println!("* Welcome to {} {}*", tool_name, " ".repeat(welcome_len - 15 - tool_name.len()));
    //         println!("* Powered by WhiteboxTools {}*", " ".repeat(welcome_len - 28));
    //         println!("* www.whiteboxgeo.com {}*", " ".repeat(welcome_len - 23));
    //         println!("{}", "*".repeat(welcome_len));
    //     }
    //
    //     let mut num_procs = num_cpus::get() as isize;
    //     let configs = whitebox_common::configs::get_configs()?;
    //     let max_procs = configs.max_procs;
    //     if max_procs > 0 && max_procs < num_procs {
    //         num_procs = max_procs;
    //     }
    //
    //     let sep: String = path::MAIN_SEPARATOR.to_string();
    let sep: String = path::MAIN_SEPARATOR.to_string();
    let configurations = whitebox_common::configs::get_configs()?;
    let verbose = configurations.verbose_mode;
    let mut working_directory = configurations.working_directory.clone();
    if !working_directory.is_empty() && !working_directory.ends_with(&sep) {
        working_directory += &sep;
    }

    if verbose {
        let tool_name = TOOL_NAME;
        let welcome_len = format!("* Welcome to {} *", tool_name).len().max(28);
        // 28 = length of the 'Powered by' by statement.
        println!("{}", "*".repeat(welcome_len));
        println!("* Welcome to {} {}*", tool_name, " ".repeat(welcome_len - 15 - tool_name.len()));
        println!("* Powered by WhiteboxTools {}*", " ".repeat(welcome_len - 28));
        println!("* www.whiteboxgeo.com {}*", " ".repeat(welcome_len - 23));
        println!("{}", "*".repeat(welcome_len));
    }

    let mut num_procs = num_cpus::get() as isize;
    let max_procs = configurations.max_procs;
    if max_procs > 0 && max_procs < num_procs {
        num_procs = max_procs;
    }

    // ADDED 2026-08-15 (Claude Opus 5, operator Daniel; plan "wbt-viewshed-offsets").
    // WHAT: refuse --curvature unless --refraction_k is given, and resolve the
    //       correction into a single coefficient.
    // WHY:  the refraction coefficient is a declared methodological parameter,
    //       not a convenience default. Baking one in would let a caller apply a
    //       correction without ever naming its strength, which is how the
    //       inconsistency this tool exists to fix arose in the first place.
    // WHY IT CANNOT MOVE A NUMBER: curv_coeff is 0 whenever --curvature is
    //       absent, so the term it multiplies drops out entirely.
    let curv_coeff: f64 = if curvature {
        let k = refraction_k.ok_or_else(|| {
            Error::new(
                ErrorKind::InvalidInput,
                "--curvature requires --refraction_k. There is deliberately no \
                 default: the refraction coefficient is a declared parameter. \
                 0.13 is the common geodetic value.",
            )
        })?;
        if earth_radius <= 0f64 {
            return Err(Error::new(
                ErrorKind::InvalidInput,
                "--earth_radius must be greater than zero.",
            ));
        }
        if z_factor <= 0f64 {
            return Err(Error::new(
                ErrorKind::InvalidInput,
                "--z_factor must be greater than zero.",
            ));
        }
        // Dividing by z_factor expresses the drop -- a length in HORIZONTAL
        // units -- in the DEM's VERTICAL units, so it can be subtracted from an
        // elevation. With the default z_factor = 1.0 this is an identity.
        (1f64 - k) / (2f64 * earth_radius * z_factor)
    } else {
        0f64
    };

    let mut progress: usize;
    let mut old_progress: usize = 1;

    if !input_file.contains(&sep) && !input_file.contains("/") {
        input_file = format!("{}{}", working_directory, input_file);
    }
    if !stations_file.contains(&sep) && !stations_file.contains("/") {
        stations_file = format!("{}{}", working_directory, stations_file);
    }
    if !output_file.contains(&sep) && !output_file.contains("/") {
        output_file = format!("{}{}", working_directory, output_file);
    }

    if verbose {
        println!("Reading data...")
    };
    let dem = Arc::new(Raster::new(&input_file, "r")?);

    let start = Instant::now();

    if height < 0f64 {
        println!("Warning: Input station height cannot be less than zero.");
        height = 0f64;
    }

    // ADDED 2026-08-15 (Claude Opus 5, operator Daniel; generalization pass).
    // WHAT: refuse --curvature and --max_dist on a DEM whose coordinates are
    //       almost certainly geographic (degrees) rather than projected.
    // WHY:  both parameters are physical lengths. Every distance in this tool is
    //       computed from the DEM's own coordinates, so on a lat/long DEM `dist`
    //       comes out in DEGREES -- making the curvature drop and the distance
    //       cap silently meaningless. Upstream Viewshed is immune because it only
    //       compares angles, where a consistent unit error cancels; these two
    //       parameters are exactly where it stops cancelling. Detection is by
    //       cell size rather than by CRS metadata, because GeoTIFF readers do not
    //       reliably populate xy_units and a wrong answer here is silent.
    // WHY IT CANNOT MOVE A NUMBER: this only ever refuses to run. It cannot
    //       reach a DEM in projected units at any plausible resolution, and it is
    //       skipped entirely when neither parameter is in use.
    let res_x = dem.configs.resolution_x;
    let looks_geographic = res_x > 0f64 && res_x < 0.01;
    if looks_geographic && (curvature || max_dist.is_finite())
        && earth_radius == 6371000f64
    {
        return Err(Error::new(
            ErrorKind::InvalidInput,
            format!(
                "This DEM has a cell size of {res_x} in its own coordinate units, \
                 which almost certainly means it is in degrees rather than a \
                 projected CRS. --curvature and --max_dist are physical lengths \
                 measured in the DEM's coordinate units, so on a geographic DEM \
                 both would be meaningless. Either reproject the DEM to a \
                 projected CRS in meters, or, if its units really are projected \
                 but not meters, pass --earth_radius in those same units to \
                 confirm the choice is deliberate."
            ),
        ));
    }

    let rows = dem.configs.rows as isize;
    let columns = dem.configs.columns as isize;
    let nodata = dem.configs.nodata;

    // let stations = Arc::new(Raster::new(&stations_file, "r")?);
    // let stations = Raster::new(&stations_file, "r")?;
    let stations = Shapefile::read(&stations_file)?;

    // make sure the input vector file is of points type
    if stations.header.shape_type.base_shape_type() != ShapeType::Point {
        return Err(Error::new(
            ErrorKind::InvalidInput,
            "The input vector data must be of point base shape type.",
        ));
    }

    let mut output = Raster::initialize_using_file(&output_file, &dem);

    // scan the stations raster and place each non-zero grid cell into Vecs
    // let mut z: f64;
    let mut station_x = vec![];
    let mut station_y = vec![];
    // for row in 0..rows {
    //     for col in 0..columns {
    //         z = stations.get_value(row, col);
    //         if z > 0f64 && dem.get_value(row, col) != nodata {
    //             station_x.push(stations.get_x_from_column(col));
    //             station_y.push(stations.get_y_from_row(row));
    //         }
    //     }

    //     if verbose {
    //         progress = (100.0_f64 * row as f64 / (rows - 1) as f64) as usize;
    //         if progress != old_progress {
    //             println!("Locating stations: {}%", progress);
    //             old_progress = progress;
    //         }
    //     }
    // }

    for record_num in 0..stations.num_records {
        let record = stations.get_record(record_num);
        station_y.push(record.points[0].y);
        station_x.push(record.points[0].x);

        if verbose {
            progress =
                (100.0_f64 * record_num as f64 / (stations.num_records - 1) as f64) as usize;
            if progress != old_progress {
                println!("Locating view stations: {}%", progress);
                old_progress = progress;
            }
        }
    }

    let (mut stn_x, mut stn_y): (f64, f64);
    let mut stn_z: f64;
    let (mut stn_row, mut stn_col): (isize, isize);
    let mut view_angle: Array2D<f32> = Array2D::new(rows, columns, -32768f32, -32768f32)?;
    // ADDED 2026-08-15 (Claude Opus 5, operator Daniel; plan "wbt-viewshed-offsets").
    // WHAT: a second angle grid holding the angle to the TOP OF THE TARGET, i.e.
    //       to ground + offset_b, allocated only when offset_b is non-zero.
    // WHY:  upstream uses one grid for two different jobs -- the angle a cell
    //       contributes to the occluding horizon, and the angle at which that
    //       cell is tested for visibility. A target offset requires separating
    //       them: raising the receiver must not also raise the terrain that
    //       blocks it. `view_angle` stays bare-earth and continues to feed the
    //       horizon sweep; `target_angle` is used only for the final test. This
    //       matches how GRASS r.viewshed treats its target offset.
    // WHY IT CANNOT MOVE A NUMBER: at offset_b == 0 the grid is not allocated
    //       (Array2D::new(0, 0, ...)) and the output loop falls back to
    //       `view_angle`, which is the upstream expression verbatim.
    let need_target = offset_b != 0f64;
    let mut target_angle: Array2D<f32> = if need_target {
        Array2D::new(rows, columns, -32768f32, -32768f32)?
    } else {
        Array2D::new(1, 1, -32768f32, -32768f32)?
    };
    let mut stn_num = 0;
    let num_stn = station_x.len();
    while !station_x.is_empty() {
        stn_num += 1;
        println!("Station {} of {}", stn_num, num_stn);

        stn_x = station_x.pop().expect("Error during pop operation.");
        stn_col = dem.get_column_from_x(stn_x);
        stn_y = station_y.pop().expect("Error during pop operation.");
        stn_row = dem.get_row_from_y(stn_y);
        // CHANGED 2026-08-15 (Claude Opus 5, operator Daniel; generalization pass).
        // WHAT: honor --station_z if given, and otherwise REFUSE when the DEM
        //       has no elevation under the station.
        // WHY:  upstream reads the DEM unconditionally, so a station on a NoData
        //       cell yields `stn_z = nodata + height`. Measured on a test DEM
        //       whose sea is NoData: a station out at sea produced an observer at
        //       -9997 m and wrote a plausible-looking raster with 300 visible
        //       cells. There was no error and no warning. That is the failure
        //       mode this guard exists for -- it is common in practice, because
        //       many DEMs encode ocean and voids as NoData.
        // THIS ONE CAN CHANGE BEHAVIOR, DELIBERATELY, AND DIVERGES FROM UPSTREAM:
        //       where upstream returns a meaningful viewshed this is byte-for-byte
        //       identical, and tests/parity.py covers that on 419,592,012 cells
        //       per station. Where upstream returns garbage, this refuses instead.
        //       Refusing is not a different answer to the same question; it is
        //       declining to answer an ill-posed one. Documented in README.
        // ORIGINAL, left in place per the code-edit marking convention:
        //     stn_z = dem.get_value(stn_row, stn_col) + height;
        let ground = match station_z {
            Some(z) => z,
            None => {
                let z = dem.get_value(stn_row, stn_col);
                if z == nodata {
                    return Err(Error::new(
                        ErrorKind::InvalidInput,
                        format!(
                            "Viewing station {} of {} sits on a NoData cell, so the \
                             DEM gives it no ground elevation. This is common where \
                             a DEM encodes ocean or voids as NoData. Either move the \
                             station onto valid terrain, fill the NoData (for marine \
                             work the sea should be real 0 m elevation, not NoData \
                             -- NoData cells do not occlude, so leaving them empty \
                             removes the sea horizon entirely), or pass --station_z \
                             to state the observer's ground elevation explicitly.",
                            stn_num, num_stn
                        ),
                    ));
                }
                z
            }
        };
        stn_z = ground + height;

        if (stn_col < 0 || stn_col >= columns) && (stn_row < 0 || stn_row >= rows) {
            return Err(Error::new(
                ErrorKind::InvalidInput,
                "The input stations is not located within the footprint of the DEM.",
            ));
        }

        // now calculate the view angle
        let (tx, rx) = mpsc::channel();
        for tid in 0..num_procs {
            let dem = dem.clone();
            let tx = tx.clone();
            thread::spawn(move || {
                let (mut x, mut y): (f64, f64);
                let mut z: f64;
                let mut dz: f64;
                let mut dist: f64;
                for row in (0..rows).filter(|r| r % num_procs == tid) {
                    let mut data: Vec<f32> = vec![-32768f32; columns as usize];
                    // CHANGED 2026-08-15 (Claude Opus 5, operator Daniel; plan
                    // "wbt-viewshed-offsets").
                    // WHAT: compute `dist` before `dz`; drop the target elevation
                    //       by the curvature/refraction term before differencing;
                    //       and emit a second angle for the raised target.
                    // WHY:  the curvature drop is a function of distance, so it
                    //       cannot be applied until `dist` exists -- hence the
                    //       reorder. The drop is subtracted from the TARGET, and
                    //       therefore applies to intervening terrain as well as
                    //       to the cell under test, which is what makes it an
                    //       occlusion-correct treatment rather than a cosmetic
                    //       one. Form is (1-k)*d^2/(2R), the drop-from-observer
                    //       expression; for any fixed observer-target pair this
                    //       is algebraically identical to the transect-bulge form
                    //       (1-k)*d*(D-d)/(2R) used by transect-based LOS engines, so
                    //       the two engines agree by construction.
                    // WHY IT CANNOT MOVE A NUMBER: `curv_coeff` is exactly 0.0
                    //       unless --curvature was given, and the branch below
                    //       then binds `z_eff = z`, so `dz = z - stn_z` is the
                    //       upstream expression unchanged -- not merely equal to
                    //       it under IEEE754, but the same operation. Reordering
                    //       `dist` earlier cannot affect `dz`, which does not
                    //       depend on it. `data_t` is empty and unread when
                    //       offset_b == 0.
                    // ORIGINAL, left in place per the code-edit marking convention:
                    //     dz = z - stn_z;
                    //     dist =
                    //         ((x - stn_x) * (x - stn_x) + (y - stn_y) * (y - stn_y)).sqrt();
                    //     if dist != 0.0 {
                    //         data[col as usize] = (dz / dist * 1000f64) as f32;
                    //     } else {
                    //         data[col as usize] = 0f32;
                    //     }
                    let mut data_t: Vec<f32> = if need_target {
                        vec![-32768f32; columns as usize]
                    } else {
                        Vec::new()
                    };
                    for col in 0..columns {
                        z = dem.get_value(row, col);
                        if z != nodata {
                            x = dem.get_x_from_column(col);
                            y = dem.get_y_from_row(row);
                            dist =
                                ((x - stn_x) * (x - stn_x) + (y - stn_y) * (y - stn_y)).sqrt();
                            let z_eff = if curv_coeff != 0f64 {
                                z - curv_coeff * dist * dist
                            } else {
                                z
                            };
                            dz = z_eff - stn_z;
                            if dist != 0.0 {
                                data[col as usize] = (dz / dist * 1000f64) as f32;
                                if need_target {
                                    data_t[col as usize] =
                                        ((dz + offset_b) / dist * 1000f64) as f32;
                                }
                            } else {
                                data[col as usize] = 0f32;
                                if need_target {
                                    data_t[col as usize] = 0f32;
                                }
                            }
                        }
                    }
                    tx.send((row, data, data_t)).unwrap();
                }
            });
        }

        for r in 0..rows {
            // CHANGED 2026-08-15 (Claude Opus 5, operator Daniel; plan
            // "wbt-viewshed-offsets"). Receive and store the second angle row.
            // WHY IT CANNOT MOVE A NUMBER: `view_angle` is filled from the same
            // `data` vector as before; the added row is written to a separate
            // grid that is unread when offset_b == 0.
            // ORIGINAL, left in place per the code-edit marking convention:
            //     let (row, data) = rx.recv().expect("Error receiving data from thread.");
            //     view_angle.set_row_data(row, data);
            let (row, data, data_t) = rx.recv().expect("Error receiving data from thread.");
            view_angle.set_row_data(row, data);
            if need_target {
                target_angle.set_row_data(row, data_t);
            }

            if verbose {
                progress = (100.0_f64 * r as f64 / (rows - 1) as f64) as usize;
                if progress != old_progress {
                    println!(
                        "Calculating view angle (Station {} of {}): {}%",
                        stn_num, num_stn, progress
                    );
                    old_progress = progress;
                }
            }
        }

        let mut max_view_angle: Array2D<f32> =
            Array2D::new(rows, columns, -32768f32, -32768f32)?;

        let mut z: f32;

        // perform the simple scan lines.
        for row in stn_row - 1..stn_row + 2 {
            for col in stn_col - 1..stn_col + 2 {
                max_view_angle.set_value(row, col, view_angle.get_value(row, col));
            }
        }

        let mut max_va = view_angle.get_value(stn_row - 1, stn_col);
        for row in (0..stn_row - 1).rev() {
            z = view_angle.get_value(row, stn_col);
            if z > max_va {
                max_va = z;
            }
            max_view_angle.set_value(row, stn_col, max_va);
        }

        max_va = view_angle.get_value(stn_row + 1, stn_col);
        for row in stn_row + 2..rows {
            z = view_angle.get_value(row, stn_col);
            if z > max_va {
                max_va = z;
            }
            max_view_angle.set_value(row, stn_col, max_va);
        }

        max_va = view_angle.get_value(stn_row, stn_col + 1);
        for col in stn_col + 2..columns {
            z = view_angle.get_value(stn_row, col);
            if z > max_va {
                max_va = z;
            }
            max_view_angle.set_value(stn_row, col, max_va);
        }

        max_va = view_angle.get_value(stn_row, stn_col - 1);
        for col in (0..stn_col - 1).rev() {
            z = view_angle.get_value(stn_row, col);
            if z > max_va {
                max_va = z;
            }
            max_view_angle.set_value(stn_row, col, max_va);
        }

        //solve the first triangular facet
        let mut tva: f32;
        let mut va: f32;
        let mut t1: f32;
        let mut t2: f32;
        let mut vert_count = 1f32;
        let mut horiz_count: f32;
        for row in (0..stn_row - 1).rev() {
            vert_count += 1f32;
            horiz_count = 0f32;
            for col in stn_col + 1..stn_col + (vert_count as isize) + 1 {
                if col <= columns {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row + 1, col - 1);
                        t2 = max_view_angle.get_value(row + 1, col);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row + 1, col - 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        //solve the second triangular facet
        vert_count = 1f32;
        for row in (0..stn_row - 1).rev() {
            vert_count += 1f32;
            horiz_count = 0f32;
            for col in (stn_col - (vert_count as isize)..stn_col).rev() {
                if col >= 0 {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row + 1, col + 1);
                        t2 = max_view_angle.get_value(row + 1, col);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row + 1, col + 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        // solve the third triangular facet
        vert_count = 1f32;
        for row in stn_row + 2..rows {
            vert_count += 1f32;
            horiz_count = 0f32;
            for col in (stn_col - (vert_count as isize)..stn_col).rev() {
                if col >= 0 {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row - 1, col + 1);
                        t2 = max_view_angle.get_value(row - 1, col);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row - 1, col + 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        // solve the fourth triangular facet
        vert_count = 1f32;
        for row in stn_row + 2..rows {
            vert_count += 1f32;
            horiz_count = 0f32;
            for col in stn_col + 1..stn_col + (vert_count as isize) + 1 {
                if col < columns {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row - 1, col - 1);
                        t2 = max_view_angle.get_value(row - 1, col);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row - 1, col - 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        // solve the fifth triangular facet
        vert_count = 1f32;
        for col in stn_col + 2..columns {
            vert_count += 1f32;
            horiz_count = 0f32;
            for row in (stn_row - (vert_count as isize)..stn_row).rev() {
                if row >= 0 {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row + 1, col - 1);
                        t2 = max_view_angle.get_value(row, col - 1);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row + 1, col - 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        // solve the sixth triangular facet
        vert_count = 1f32;
        for col in stn_col + 2..columns {
            vert_count += 1f32;
            horiz_count = 0f32;
            for row in stn_row + 1..stn_row + (vert_count as isize) + 1 {
                if row < rows {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row - 1, col - 1);
                        t2 = max_view_angle.get_value(row, col - 1);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row - 1, col - 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        // solve the seventh triangular facet
        vert_count = 1f32;
        for col in (0..stn_col - 1).rev() {
            vert_count += 1f32;
            horiz_count = 0f32;
            for row in stn_row + 1..stn_row + (vert_count as isize) + 1 {
                if row < rows {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row - 1, col + 1);
                        t2 = max_view_angle.get_value(row, col + 1);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row - 1, col + 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        // solve the eighth triangular facet
        vert_count = 1f32;
        for col in (0..stn_col - 1).rev() {
            vert_count += 1f32;
            horiz_count = 0f32;
            for row in (stn_row - (vert_count as isize)..stn_row).rev() {
                if row < rows {
                    va = view_angle.get_value(row, col);
                    horiz_count += 1f32;
                    if horiz_count != vert_count {
                        t1 = max_view_angle.get_value(row + 1, col + 1);
                        t2 = max_view_angle.get_value(row, col + 1);
                        tva = t2 + horiz_count / vert_count * (t1 - t2);
                    } else {
                        tva = max_view_angle.get_value(row + 1, col + 1);
                    }
                    if tva > va {
                        max_view_angle.set_value(row, col, tva);
                    } else {
                        max_view_angle.set_value(row, col, va);
                    }
                } else {
                    break;
                }
            }
        }

        let mut value: f64;
        for row in 0..rows {
            for col in 0..columns {
                // z = max_view_angle.get_value(row, col);
                // if z > -32768f32 {
                //     output.set_value(row, col, z as f64);
                // } else {
                //     output.set_value(row, col, nodata);
                // }
                if dem.get_value(row, col) != nodata {
                    // CHANGED 2026-08-15 (Claude Opus 5, operator Daniel; plan
                    // "wbt-viewshed-offsets").
                    // WHAT: test the cell against `target_angle` rather than
                    //       `view_angle` when a target offset is in force, and
                    //       report cells beyond --max_dist as zero.
                    // WHY:  this is the point where the two roles of `view_angle`
                    //       separate. `max_view_angle` is the horizon built from
                    //       bare-earth angles and is left exactly as upstream
                    //       computed it; only the angle it is compared AGAINST
                    //       changes, to the angle subtended by the top of the
                    //       target. The distance test masks to a disc, not to the
                    //       bounding square, so "within D" means within D.
                    // WHY IT CANNOT MOVE A NUMBER: when offset_b == 0,
                    //       `need_target` is false and `test_angle` binds to
                    //       `view_angle.get_value(row, col)` -- the upstream
                    //       expression verbatim. When max_dist is infinite the
                    //       distance test is `dist > inf`, which is false for
                    //       every finite dist, so `beyond_cap` is false everywhere
                    //       and the branch is inert. Cells beyond the cap affect
                    //       the output only; they still occluded normally during
                    //       the sweep, so no retained cell's value depends on the
                    //       cap. Verified by tests/behavior.py, which requires a
                    //       capped run to equal an uncapped run inside d <= D and
                    //       to report zero outside it.
                    // ORIGINAL, left in place per the code-edit marking convention:
                    //     value = if max_view_angle.get_value(row, col)
                    //         > view_angle.get_value(row, col)
                    //     {
                    //         0f64
                    //     } else {
                    //         1f64
                    //     };
                    //     output.increment(row, col, value);
                    let mut beyond_cap = false;
                    if max_dist.is_finite() {
                        let x = dem.get_x_from_column(col);
                        let y = dem.get_y_from_row(row);
                        let dist =
                            ((x - stn_x) * (x - stn_x) + (y - stn_y) * (y - stn_y)).sqrt();
                        beyond_cap = dist > max_dist;
                    }
                    let test_angle = if need_target {
                        target_angle.get_value(row, col)
                    } else {
                        view_angle.get_value(row, col)
                    };
                    // A cell beyond the cap counts as not-visible-from-this-station
                    // rather than as nodata. The output is a COUNT of stations, so
                    // 0 is a true and meaningful answer -- "no station sees this
                    // cell within its declared viewing distance" -- whereas nodata
                    // would mean the cell could not be computed. Writing nodata
                    // here would also make the nodata mask depend on --max_dist,
                    // so a capped run could no longer be compared cell-for-cell
                    // with an uncapped one.
                    value = if beyond_cap || max_view_angle.get_value(row, col) > test_angle {
                        0f64
                    } else {
                        1f64
                    };
                    output.increment(row, col, value);
                }
            }

            if verbose {
                progress = (100.0_f64 * row as f64 / (rows - 1) as f64) as usize;
                if progress != old_progress {
                    println!(
                        "Creating output: (Station {} of {}): {}%",
                        stn_num, num_stn, progress
                    );
                    old_progress = progress;
                }
            }
        }
    }

    let elapsed_time = get_formatted_elapsed_time(start);
    // CHANGED 2026-08-15 (Claude Opus 5, operator Daniel; plan "wbt-viewshed-offsets").
    // WHAT: name the tool from a constant, and record the geometry parameters in
    //       the output's metadata.
    // WHY:  a raster produced under a target offset, a curvature correction or a
    //       distance cap is not comparable with one produced without them, and
    //       the file should say which it is rather than relying on a filename
    //       suffix or on someone's memory of the invocation.
    // WHY IT CANNOT MOVE A NUMBER: metadata strings only; no cell value is read
    //       or written here.
    // ORIGINAL, left in place per the code-edit marking convention:
    //     output.add_metadata_entry(format!(
    //         "Created by whitebox_tools\' {} tool",
    //         self.get_tool_name()
    //     ));
    output.add_metadata_entry(format!("Created by whitebox_tools\' {} tool", TOOL_NAME));
    output.add_metadata_entry(format!("DEM file: {}", input_file));
    output.add_metadata_entry(format!("offset_a (station height): {}", height));
    output.add_metadata_entry(format!("offset_b (target height): {}", offset_b));
    output.add_metadata_entry(format!(
        "curvature: {}",
        if curvature {
            format!(
                "applied, k={}, R={}",
                refraction_k.expect("refraction_k is Some whenever curvature is true"),
                earth_radius
            )
        } else {
            "not applied".to_string()
        }
    ));
    output.add_metadata_entry(format!(
        "max_dist: {}",
        if max_dist.is_finite() {
            format!("{}", max_dist)
        } else {
            "unlimited".to_string()
        }
    ));
    output.add_metadata_entry(format!("Elapsed Time (excluding I/O): {}", elapsed_time));

    if verbose {
        println!("Saving data...")
    };
    let _ = match output.write() {
        Ok(_) => {
            if verbose {
                println!("Output file written")
            }
        }
        Err(e) => return Err(e),
    };
    if verbose {
        println!(
            "{}",
            &format!("Elapsed Time (excluding I/O): {}", elapsed_time)
        );
    }

    Ok(())
}
