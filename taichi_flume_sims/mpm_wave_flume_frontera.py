#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Added above lines for external execution of the script
import taichi as ti
from taichi import tools
import numpy as np
import platform # For getting the operating system name, taichi may already have something for this
import os
import json
import math
import imageio
import time as T
from matplotlib import cm
from pyevtk.hl import pointsToVTK
import point_cloud_utils as pcu
import argparse

# import save_sim as ss # Using the save_simulation() function present in current file instead 
from Simulation_validation import SolitonWaveValidation
ti.init(arch=ti.gpu)  # Try to run on GPU

# ------------------------ Output Style ------------------------
# Output configuration for GUI and simulation files
use_vulkan_gui = False # Needed for Windows WSL currently, and any non-vulkan systems - Turn on if you want to use the faster new GUI renderer
output_gui = False # Output to GUI window (original, not GGUI which requires vulkan for GPU render)
output_png = True # Outputs png files and makes a gif out of them

# output_particles_as_ply = True # Outputs particles at individual frames as ply files
# output_particles_as_bgeo = True # Outputs particles at individual frames as xyz files
# output_particles_as_npy = True # Outputs particles at individual frames as npy files
output_particles_as_npz = True # Outputs particles at all frames as a single npz file at the end of the simulation
output_particles_as_vtk = True # Outputs particles at individual frames as vtk files
output_particles_as_filetype = ["npz", "vtk"]
support_output_particle_filetypes = ["npz", "vtk"] # "ply", "bgeo", "npy", "npz", "vtk"
if not any([x in support_output_particle_filetypes for x in output_particles_as_filetype]):
    raise ValueError("Invalid output particle filetype selected. Please select from: ", support_output_particle_filetypes)

print("Output frames to GUI window{}, and PNG files{}".format(" enabled" if output_gui else " disabled", " enabled" if output_png else " disabled"))

# ------------------------ Simulation Domain Setup ------------------------
dim = 3
if (dim == '2d' or int(dim) == 2):
    DIMENSIONS = 2
    print("2D Simulation Selected")
elif (dim == '3d' or int(dim) == 3):
    DIMENSIONS = 3
    print("3D Simulation Selected")
else:
    raise ValueError("Invalid dimensionality selected. Please select 2D or 3D.")

# Configure simulation resolution and memory allocation
system = platform.system().lower() # Useful for defining the sim environment
on_weak_pc = False # Set to True if running on a weak PC, False if running on a strong PC (lots of GPU memory good speed)
preallocate_memory_style = "manual"

n_grid = 1024 # Default number of grid nodes in a direction
particles_per_dx = 4 # Default number of particles per dx, e.g. 4 -> 4^d = 16 or 64 particles per cell in 2d and 4d
particles_per_cell = particles_per_dx ** DIMENSIONS
print("NOTE: Common initial Particles-per-Cell, (PPC), are {}, {}, {}, or {}.".format(1**DIMENSIONS, 2**DIMENSIONS, 3**DIMENSIONS, 4**DIMENSIONS))
particles_per_cell =float(64)
particles_per_dx = int(round(particles_per_cell ** (1 / DIMENSIONS))) # get the inverse power of the particles per cell to get the particles per dx, rounded to the nearest integer

if preallocate_memory_style == "bits":
    # More bits = higher resolution, more accurate simulation, but slower and more memory usage
    quality = 6 # Resolution multiplier that affects both particles and grid nodes by multiplying their base units w.r.t. dimensions
    particle_quality_bits = 13 # Bits for particle count base unit, e.g. 13 = 2^13 = 8192 particles
    grid_quality_bits = 7 # Bits for grid nodes base unit in a direction, e.g. 7 = 2^7 = 128 grid nodes in a direction

    n_grid_base = 2 ** grid_quality_bits # Using pow-2 grid-size for improved GPU mem usage / performance 
    n_grid = n_grid_base * quality


if preallocate_memory_style == "manual":
    # check OS to see if its ubuntu
    n_grid = 1024 # For 102.4 m domain, 102.4 / n_grid cm grid spacing 
    if (system != 'linux' and system != 'linux2') and on_weak_pc:
        n_grid = min(512, n_grid) # For weak PCs, decrease the grid size to 512 to save memory


buffer_cells = 3  # Number of buffer cells to add around sides of the simulation domain

# ------------------------ Algorithm Setup ------------------------
# Set shape-function kernel's order for MPM algorithm
bspline_kernel_order = 2 # Quadratic BSpline kernel
supported_bspline_orders = [2] # Quadratic, Cubic, Quartic BSpline kernels
if bspline_kernel_order not in supported_bspline_orders:
    raise ValueError("Invalid BSpline kernel order selected. Please select from: ", supported_bspline_orders)

# Configure volumetric antilocking for MPM pressure projection, improves pressure accuracy (i.e. smooth pressure field)
use_antilocking = True # Use anti-locking for improved pressures
JB_fluid_ratio = 0.95 # Amount of antilocking, [0,1]. Recc < 0.9, < 0.99, < 0.999 for bulk of ~2e7, ~2e8, and ~2e9
if JB_fluid_ratio > 1.0 or JB_fluid_ratio < 0.0:
    raise ValueError("Invalid mixing ratio selected for F-Bar vol. antilocking pressure smoothing: [", JB_fluid_ratio, "]. Please select from: [0,1].")


# ------------------------ Flume Setup ------------------------
flume_shorten_ratio = 1.0 # Halve the flume length for testing purposes
flume_thin_ratio = 0.0625 / 4
if (system != 'linux' and system != 'linux2') and on_weak_pc:
    flume_shorten_ratio = 1.0 # Halve the flume length for testing purposes
    weak_pc_max_flume_width_ratio = 0.125
    flume_thin_ratio = min(weak_pc_max_flume_width_ratio, flume_thin_ratio)
    print("Running on a weak PC, reducing width by ratio of: ", flume_thin_ratio)
if flume_shorten_ratio > 1.0 or flume_shorten_ratio < 0.0:
    raise ValueError("Invalid flume length ratio selected. Please select from: [0,1].")
if flume_thin_ratio > 1.0 or flume_thin_ratio < 0.0:
    raise ValueError("Invalid flume width ratio selected. Please select from: [0,1].")

if dim == '3d' or int(dim) == 3:
    DIMENSIONS = 3
    # Wave Flume Render 3d using grid_length as the flume length in 2D & 3D
    # https://engineering.oregonstate.edu/wave-lab/facilities/large-wave-flume
    flume_length = 90 * flume_shorten_ratio # meters
    flume_height = 4.6 # meters
    flume_width = 3.6 * flume_thin_ratio # meters

else:
    DIMENSIONS = 2
    # Wave Flume Render 3d using grid_length as the flume length in 2D & 3D
    # https://engineering.oregonstate.edu/wave-lab/facilities/large-wave-flume
    flume_length = 90 * flume_shorten_ratio # meters
    flume_height = 4.6 # meters
    flume_width = 0.0 # meters
    
# Max_water_depth_wind_storm = 2.7 # meters
# Define Taichi fields for flume geometry
flume_vertices = ti.Vector.field(3, dtype=float, shape=8)
bottom = ti.field(dtype=int, shape=6)  # Change to a 1D array
# Only way to render flume with differing colors
backwall = ti.field(dtype=int, shape=6)
frontwall = ti.field(dtype=int, shape=6)
sidewalls = ti.field(dtype=int, shape=6)

front_back_color = (166/255, 126/255, 71/255)  # Rust color in RGB
side_color = (197/255, 198/255, 199/255)  # Light grey color in RGB
bottom_color = (79/255, 78/255, 78/255)
background_color = (237/255, 235/255, 235/255)



# Define the longest scale of the simulation domain
grid_length = 102.4 * flume_shorten_ratio  # Max length of the simulation domain in any direction [meters]
dx, inv_dx = float(grid_length / n_grid), float(n_grid / grid_length)

# ------------------------ Simulation Resolution Setup ------------------------
# Define the grid ratios for each dimension
#Using a shorter length means that grid_ratio_y and z may need to increase to be closer to 1 since the domain becomes more like a cube as opposed to a long flume. May be better to work with the full length for now
# Best to use powers of 2 for mem allocation, e.g. 0.5, 0.25, 0.125, etc. 
# Note: there are buffer grid-cells on either end of each dimension for multi-cell shape-function kernels and BCs
grid_ratio_x = 1.0000
grid_ratio_y = 0.25
grid_ratio_z = 0.125
# grid_ratio_y = (2**(math.ceil((flume_height / grid_length) * n_grid) - 1).bit_length()) / n_grid
# grid_ratio_z = (2**(math.ceil((flume_width / grid_length) * n_grid) - 1).bit_length()) / n_grid
print("Grid Ratios: ", grid_ratio_x, grid_ratio_y, grid_ratio_z)
grid_length_x = grid_length * ti.max(0.0, ti.min(1.0, grid_ratio_x))
grid_length_y = grid_length * ti.max(0.0, ti.min(1.0, grid_ratio_y))
grid_length_z = grid_length * ti.max(0.0, ti.min(1.0, grid_ratio_z))
print("Domain Dimensions", grid_length_x, grid_length_y, grid_length_z)
n_grid_x = int(ti.max(n_grid * ti.min(grid_ratio_x, 1), 1))
n_grid_y = int(ti.max(n_grid * ti.min(grid_ratio_y, 1), 1))
n_grid_z = int(ti.max(n_grid * ti.min(grid_ratio_z, 1), 1))
# n_grid_total = int(ti.max(n_grid_x,1) * ti.max(n_grid_y,1)) 
n_grid_total = int(ti.max(n_grid_x,1) * ti.max(n_grid_y,1) * ti.max(n_grid_z,1)) # Define this to work in 2d and 3d


print("Particles-per-Dx: ", particles_per_dx)
print("Particles-per-Cell: ", particles_per_cell)
particle_spacing_ratio = 1.0 / particles_per_dx
particle_spacing =  dx * particle_spacing_ratio
particle_volume_ratio = 1.0 / particles_per_cell
particle_volume = (dx ** DIMENSIONS) * particle_volume_ratio

n_particles_default = 81000
n_particles = n_particles_default
set_particle_count_style = "auto" # "auto" or "manual" or "optimized" or "compiled"


if (set_particle_count_style == "manual"):
    n_particles = int(input("Number of Particles to Simulate: "))
    print("Number of Particles: ", n_particles)
    
elif (set_particle_count_style == "optimized"):
    n_particles_base = 2 ** particle_quality_bits # Better ways to do this, shouldnt have to set it manually
    n_particles = n_particles_base * (quality**DIMENSIONS)
    print("Number of Particles: ", n_particles)

elif (set_particle_count_style == "compiled" or set_particle_count_style == "default"):    
    n_particles = n_particles_default


# ------------------------ Piston / Moving-Boundary Setup ------------------------
# Piston Physics
paper = "Bonus 2023"
experiment = "breaking"

if experiment == "breaking" and (paper == "Mascarenas 2022" or paper == "Bonus 2023"):
    piston_amplitude = 3.6 # 4 meters max range on piston's stroke in the OSU LWF
    piston_scale_factor = 1.0 # Standard deviation scaling of the piston motion's error-function
    piston_period = piston_scale_factor*3.14159265359 * 2# Period of the piston's motion [s]
    max_water_depth_tsunami = 1.85 # SWL from Mascardenas 2022 for breaking wave, and for Shekhar et al 2020 I believe
    wave_height_expected = 1.3 # Expected wave height for the breaking wave in meters
    wave_length_expected = 2*3.14159265359 # Expected wave length for the breaking wave in meters
    time_shift_piston = 2.5 # Time shift for the piston motion's error-function, experiments used ~10 seconds to ensure a smooth start to the wave

elif experiment == "unbreaking" and (paper == "Mascarenas 2022" or paper == "Bonus 2023"):
    piston_amplitude = 3.9 # 4 meters max range on piston's stroke in the OSU LWF
    piston_scale_factor = 5.0
    piston_period = piston_scale_factor*3.14159265359 * 2 # Period of the piston's motion [s]
    max_water_depth_tsunami = 2.0 # SWL from Mascardenas 2022 for breaking wave, and for Shekhar et al 2020 I believe
    wave_height_expected = 0.2
    wave_length_expected = (piston_scale_factor*2*3.14159265359)**0.5 # Expected wave length for the tsunami wave in meters
    time_shift_piston = 10.0 # Time shift for the piston motion's error-function, experiments used ~10 seconds to ensure a smooth start to the wave

else:
    piston_amplitude = np.pi # 4 meters max range on piston's stroke in the OSU LWF
    piston_scale_factor = 1.0
    piston_time_mean = 3.14159265359
    max_water_depth_tsunami = 2.0 # SWL maximum for tsunami irregular wave at OSU LWF [m], though the flume can accept more water it may affect the wave generation
    piston_period = piston_scale_factor*3.14159265359 # Period of the piston's motion [s] (DEPRECATED?)
    wave_height_expected = 1.0 # Expected wave height for the tsunami wave in meters
    wave_length_expected = 2*3.14159265359 # Expected wave length for the tsunami wave in meters
    time_shift_piston = 1.0 # Time shift for the piston motion's error-function, experiments used ~10 seconds to ensure a smooth start to the wave


piston_motion_sample_frequency = 120.0 # Sampling frequency of the piston motion's in experimental data on DesignSafe for Mascarenas 2022
piston_time_mean = (piston_scale_factor * np.pi // 120.0) * 120.0 + time_shift_piston
piston_time_stdev = piston_scale_factor * 0.707106781187 # Std. dev. for the piston motion's error-function, SF / sqrt(2)

# piston_period = float(180.0)
piston_start_x = 1 * dx / grid_length
piston_pos = np.array([0.0, 0.0, 0.0]) + (grid_length * np.array([piston_start_x / grid_length, 0.0, 0.0])) + (dx * np.array([buffer_cells, buffer_cells, buffer_cells])) # Initial [X,Y,Z] position of the piston face
piston_travel_x = piston_amplitude / grid_length
piston_wait_time = 0.0 # don't immediately start the piston, let things settle with gravity first


# ------------------------ Bathymetry Setup ------------------------
# Bathymetry ramp setup for the flume, remove particles under ramps to save memory
use_bathymetry_ramps = True
bathymetry_style = "linear"
piston_neutral_x = -2.0 # Neutral position of the piston from experiment coordinate system, [m]
bathymetry_joints_x = []
bathymetry_joints_x.append(0.0)
bathymetry_joints_x.append(14.275 - piston_neutral_x)
bathymetry_joints_x.append(17.933 - piston_neutral_x) # 17.933
# bathymetry_joints_x.append(17.933 - piston_neutral_x)
bathymetry_joints_x.append(28.906 - piston_neutral_x)
bathymetry_joints_x.append(43.536 - piston_neutral_x)
bathymetry_joints_x.append(80.106 - piston_neutral_x)
bathymetry_joints_x.append(87.46- piston_neutral_x)
bathymetry_joints_x.append(grid_length_x)

bathymetry_joints_y = []
bathymetry_joints_y.append(0.2)
# bathymetry_joints_y.append(0.0) # 0.226
bathymetry_joints_y.append(0.2) # top-left (xy) corner of initial raised concrete slab
bathymetry_joints_y.append(0.2)
bathymetry_joints_y.append(1.14042)
bathymetry_joints_y.append(1.75)
bathymetry_joints_y.append(1.75)
bathymetry_joints_y.append(2.3628)
bathymetry_joints_y.append(2.3628)

if len(bathymetry_joints_x) != len(bathymetry_joints_y):
    raise ValueError("Bathymetry joint x and y arrays must be the same length")
if len(bathymetry_joints_x) < 2:
    raise ValueError("Bathymetry joint x and y arrays must have at least 2 points")

NUM_JOINTS = len(bathymetry_joints_x)
NUM_RAMPS = int(max(0,NUM_JOINTS - 1))

for (i, bath_x) in enumerate(bathymetry_joints_x):
    bathymetry_joints_x[i] += buffer_cells * dx
for (j, bath_y) in enumerate(bathymetry_joints_y):
    bathymetry_joints_y[j] += buffer_cells * dx

bathymetry_joints_taichi_x = ti.Vector.field(n=NUM_JOINTS, dtype=float, shape=())
bathymetry_joints_taichi_y = ti.Vector.field(n=NUM_JOINTS, dtype=float, shape=())
bathymetry_joints_taichi_x[None] = bathymetry_joints_x
bathymetry_joints_taichi_y[None] = bathymetry_joints_y


# ------------------------ Particle Setup ------------------------
buffer_shift_particles = -1.5 # How many cells to shift the particles to account for position of the actual buffer nodes 
xyz_water = np.mgrid[(piston_pos[0] + buffer_shift_particles*dx + particle_spacing/2):(flume_length + buffer_cells*dx + buffer_shift_particles*dx - particle_spacing/2):particle_spacing, (buffer_cells*dx + buffer_shift_particles*dx + particle_spacing/2):(max_water_depth_tsunami + buffer_cells*dx + buffer_shift_particles*dx - particle_spacing/2):particle_spacing,  (buffer_cells*dx + buffer_shift_particles*dx + particle_spacing/2):(flume_width + buffer_cells*dx + buffer_shift_particles*dx - particle_spacing/2):particle_spacing].reshape(3, -1).T

# Check if the water particles are under the bathymetry ramps
water_below_bathymetry_mask = np.zeros(xyz_water.shape[0], dtype=bool)
if use_bathymetry_ramps:
    # If below the ramp then remove the particle
    for i in range(NUM_RAMPS):
        for p in range(xyz_water.shape[0]):
            in_range_x = (xyz_water[p, 0] >= bathymetry_joints_x[i] + buffer_shift_particles * dx) 
            in_range_x &= (xyz_water[p, 0] < bathymetry_joints_x[i + 1] + buffer_shift_particles * dx)
            if not in_range_x:
                continue
            
            in_range_y = (xyz_water[p, 1] < bathymetry_joints_y[i + 1] + buffer_shift_particles * dx)
            if not in_range_y:
                continue
            
            bathymetry_slope = (bathymetry_joints_y[i + 1] - bathymetry_joints_y[i]) / (bathymetry_joints_x[i + 1] - bathymetry_joints_x[i])
            allowable_depth_below_ramp = 0.0 * dx
            below_ramp_surface = (xyz_water[p,1] < bathymetry_slope * (xyz_water[p, 0] - bathymetry_joints_x[i] + buffer_shift_particles * dx) + bathymetry_joints_y[i] + buffer_shift_particles * dx - allowable_depth_below_ramp) 
            water_below_bathymetry_mask[p] = below_ramp_surface
    
    print("Water Particles Before Applying Bathymetry: ", xyz_water.shape[0])
    xyz_water = xyz_water[~water_below_bathymetry_mask]
    print("Water Particles Below Bathymetry Removed: ", water_below_bathymetry_mask.sum())


xyz_water = xyz_water[np.lexsort((xyz_water[:,2], xyz_water[:,1], xyz_water[:,0]))]   

# Downsample a point cloud so that all the points are separated by approximately a fixed value
# i.e. the downsampled points follow a blue noise distribution
# idx is an array of integer indices into v indicating which samples to keep
downsampling = True
downsampling_ratio = 100 # Downsamples by 100x
print("Downsampling: {}".format(" enabled" if downsampling else "disabled"))

xyz_water_sampled = None
xyz_debris_sampled = None
idx_water_sampled = None
idx_debris_sampled = None
xyz_sampled = None
idx_sampled = None

if downsampling:
    downsampling_radius_water = float((downsampling_ratio)**(1/DIMENSIONS) * particle_spacing)
    idx_water_sampled = pcu.downsample_point_cloud_poisson_disk(xyz_water, downsampling_radius_water)
    xyz_water_sampled = xyz_water[idx_water_sampled] # Use the indices to get the sample positions 
    # n_water_sampled = n[idx] # Use the indices to get the sample  normals
    print("Downsampled Water Particles: ", xyz_water_sampled.shape[0], " , Original Water Particles: ", xyz_water.shape[0])

n_particles_water = xyz_water.shape[0]
print("Number of Water Particles: ", n_particles_water)

debris_dimensions = 1.0 * np.array([0.5, 0.05, 0.1]) # Debris dimensions in meters
debris_array = np.array([8, 1, 1]) # Number of debris in the debris-field in each direction
debris_spacing_gap = 5 * np.array([dx, dx, dx]) # Spacing between faces of debris in the debris-field, 
debris_field_downstream_edge = 43.8 # Downstream edge of the debris field in meters, from Mascerenas 2022 experiments 
debris_water_gap = dx # Gap between water and debris in, Y direction, to avoid overlap/stickiness
debris_spacing = debris_dimensions + debris_spacing_gap # Spacing between centers of debris in the debris-field
debris_field_dimensions = debris_spacing * debris_array - debris_spacing_gap # Dimensions of the debris-field
debris_offset = np.array([debris_field_downstream_edge - debris_field_dimensions[0], max_water_depth_tsunami + debris_water_gap, (flume_width - debris_field_dimensions[2]) / 2.0]) + np.array([buffer_cells*dx + buffer_shift_particles*dx, buffer_cells*dx + buffer_shift_particles*dx, buffer_cells*dx + buffer_shift_particles*dx]) # Offset of the debris-field min corner from the origin, incl. domain buffer

# Make an array to hold all the particle positions for a piece of debris
xyz_debris = np.mgrid[(debris_offset[0] + particle_spacing/2):(debris_offset[0] + debris_dimensions[0] - particle_spacing/2):particle_spacing, (debris_offset[1] + particle_spacing/2):(debris_offset[1] + debris_dimensions[1] - particle_spacing/2):particle_spacing, (debris_offset[2] + particle_spacing/2):(debris_offset[2] + debris_dimensions[2] - particle_spacing/2):particle_spacing].reshape(3, -1).T 
n_particles_debris = xyz_debris.shape[0]

# Make an array to hold all the particle positions for the debris. It will make debris_array number of xyz_debris particle positions with the correct spacing
xyz_debris_group = np.zeros((debris_array[0] * debris_array[1] * debris_array[2] * xyz_debris.shape[0], 3))
for i in range(debris_array[0]):
    for j in range(debris_array[1]):
        for k in range(debris_array[2]):
            xyz_debris_group[(i * debris_array[1] * debris_array[2] + j * debris_array[2] + k) * xyz_debris.shape[0]:(i * debris_array[1] * debris_array[2] + j * debris_array[2] + k + 1) * xyz_debris.shape[0], :] = xyz_debris + np.array([i * debris_spacing[0], j * debris_spacing[1], k * debris_spacing[2]])

xyz_debris_group = xyz_debris_group[np.where((xyz_debris_group[:, 2] >= dx * (buffer_cells + buffer_shift_particles)) & (xyz_debris_group[:, 2] <= flume_width + dx * (buffer_cells + buffer_shift_particles)))] # Remove debris below the water surface
xyz_debris_group = xyz_debris_group[np.lexsort((xyz_debris_group[:,2], xyz_debris_group[:,1], xyz_debris_group[:,0]))]   
n_particles_debris_group = xyz_debris_group.shape[0]


# Downsample a point cloud so that all the points are separated by approximately a fixed value
# i.e. the downsampled points follow a blue noise distribution
# idx is an array of integer indices into v indicating which samples to keep
# downsampling = True
# downsampling_ratio = 1000 # Downsamples by 100x
# print("Downsampling: {}".format(" enabled" if downsampling else "disabled"))
if downsampling:
    downsample_radius_debris = 2.0 * particle_spacing
    idx_debris_sampled = pcu.downsample_point_cloud_poisson_disk(xyz_debris, downsample_radius_debris)
    xyz_debris_sampled = xyz_debris[idx_debris_sampled] # Use the indices to get the sample positions 
    # n_debris_sampled = n[idx] # Use the indices to get the sample  normals
    print("Downsampled debris Particles: ", xyz_debris_sampled.shape[0], " , Original debris Particles: ", xyz_debris.shape[0])


print("Debris Dimensions: ", debris_dimensions)
print("Debris Array: ", debris_array)
print("Debris Spacing Gaps: ", debris_spacing_gap)
print("Debris Group Dimensions: ", debris_field_dimensions)
print("Debris Group Offset: ", debris_offset)
print("XYZ Debris Shape: ", xyz_debris.shape)
print("XYZ Debris Group Shape: ", xyz_debris_group.shape)

max_base_y = np.max(xyz_water[:, 1]) # Gets Baseline y value before wave might change how I get this in main loop.

# Combine the water and debris particle positions
xyz = np.concatenate((xyz_water, xyz_debris_group), axis=0)

xyz_sampled = np.concatenate((xyz_water_sampled, xyz_debris_sampled), axis=0)
idx_sampled = np.concatenate((idx_water_sampled, idx_debris_sampled), axis=0)

if DIMENSIONS == 3:
    if set_particle_count_style == "auto" or "automatic":
        n_particles = xyz.shape[0]
else:
    n_particles = 20000

print("Total Number of Particles: ", n_particles)
print(f"Max Initialized Water Particle Height: {max_base_y}")

# xyz_numpy = np.zeros((n_particles, 3))
# xyz_numpy[:xyz.shape[0], :] = xyz
# print("XYZ Numpy: ", xyz_numpy)
# print("XYZ Numpy Shape: ", xyz_numpy.shape)


# ------------------------ Particle Material Setup ------------------------
# n_particles_water = (flume_height, flume_height, flume_width, flume_length)



print("Number of Grid-Nodes each Direction: ", n_grid_x, n_grid_y, n_grid_z)
print("dx: ", dx)


# Material Ids
material_id_dict_gns = { "Water": 5, "Sand": 6, "Debris": 0, "Plastic": 0,
                    "Piston": 0, "Boundary": 3} # GNS Mapping Dict from Dr. Kumar

material_id_dict_mpm = { "Water": 0, "Snow": 1, "Debris": 2, "Plastic": 2, "Sand": 3, "Steel": 4, 
                    "Piston": 5, "Boundary": 6} # Taichi/our Material Mapping
    
material_id_numpy = np.zeros(n_particles)
material_id_numpy[:n_particles_water] = material_id_dict_mpm["Water"]
material_id_numpy[n_particles_water:(n_particles_water + n_particles_debris_group)] = material_id_dict_mpm["Debris"]

# Material properties
p_vol, p_rho = particle_volume, 1000.0
p_mass = p_vol * p_rho
E, nu = 2.5e7, 0.25  # Young's modulus and Poisson's ratio
# TODO: Define material laws for various materials
gamma_water = 7.125 #Ratio of specific heats for water 
mu_0, lambda_0 = E / (2 * (1 + nu)), E * nu / ((1 + nu) * (1 - 2 * nu))  # Lame parameters
fps = 20
time_delta = 1.0 / fps


# Calc timestep based on elastic moduli of materials
CFL = 0.5 # CFL stability number. Typically 0.3 - 0.5 is good
bulk_modulus = E / (3 * (1 - 2 * nu))  # Bulk modulus
max_vel = math.sqrt( max(abs(bulk_modulus), 1.0) / max(abs(p_rho), 1.0) ) # Speed of sound in the material
critical_time_step = CFL * dx / max_vel # Critical time step for stability in explicit time-integration rel. to pressure wave speed
scaled_time_step = critical_time_step * 1.0 # may need to adjust based on the domain size
print("Critical Time Step: ", critical_time_step)
print("Scaled Time Step: ", scaled_time_step)
set_dt_to_critical = True
if set_dt_to_critical:
    print("Using CFL condition for time-step (dt)...")
    # CFL condition for explicit time-integration
    dt = scaled_time_step
else:
    print("Using fixed time-step (dt)...")
    # Manual
    dt = 1e-4 / max(abs(quality),1)
print("dt = ", dt)


#Added parameters for piston and particle interaction
boundary_color = 0xEBACA2 # 
board_states = ti.Vector.field(n=DIMENSIONS, dtype=float, shape=())
# board_states.from_numpy(np.array([piston_pos[0], piston_pos[1], piston_pos[2]]))
board_states[None] = [float(piston_pos[0]), float(piston_pos[1]), float(piston_pos[2])]
board_velocity = ti.Vector.field(n=DIMENSIONS, dtype=float, shape=())
board_velocity[None] = [0.0, 0.0, 0.0]
time = 0.0


# ------------------------ Simulation State Setup ------------------------
#Define some parameters we would like to track
data_to_save = [] #used for saving positional data for particles 
v_data_to_save = []

bounds = [[0.0 + buffer_cells / n_grid, flume_length / grid_length + buffer_cells / n_grid], [0.0 + buffer_cells / n_grid, flume_height / grid_length + buffer_cells / n_grid], [0.0 + buffer_cells / n_grid, flume_width / grid_length + buffer_cells / n_grid]] # For 3D

# bounds = [[0.1, 0.9], [0.1, 0.9], [0.1, 0.9]] # For 3D
vel_mean = []
vel_std = []
acc_mean = []
acc_std = []

# ------------------------ Taichi Fields State Setup ------------------------
gravity = ti.Vector.field(n=DIMENSIONS, dtype=float, shape=()) # Gravity vector, [m/s^2]

x = ti.Vector.field(DIMENSIONS, dtype=float, shape=n_particles)  # position
v = ti.Vector.field(DIMENSIONS, dtype=float, shape=n_particles)  # velocity
C = ti.Matrix.field(DIMENSIONS, DIMENSIONS, dtype=float, shape=n_particles)  # affine velocity field
F = ti.Matrix.field(DIMENSIONS, DIMENSIONS, dtype=float, shape=n_particles)  # deformation gradient
material = ti.field(dtype=int, shape=n_particles)  # material id
Jp = ti.field(dtype=float, shape=n_particles)  # plastic deformation
if ti.static(use_antilocking):
    JBar = ti.field(dtype=float, shape=n_particles)  # plastic deformation

if system == 'darwin':  # 'Darwin' is the system name for macOS
    x.from_numpy( xyz.astype(np.float32) ) # Load in the particle positions we made for the water and debris field
else:
    x.from_numpy( xyz.astype(float) ) # Load in the particle positions we made for the water and debris field

xyz = None # Clear the numpy array to save memory
material.from_numpy( material_id_numpy.astype(int) ) # Load in the material ids for the water and debris field
material_id_numpy = None # Clear the numpy array to save memory

if DIMENSIONS == 2:
    grid_tuple = (n_grid_x, n_grid_y)
elif DIMENSIONS == 3:
    grid_tuple = (n_grid_x, n_grid_y, n_grid_z)

grid_v = ti.Vector.field(DIMENSIONS, dtype=float, shape=grid_tuple)  # grid node momentum/velocity
grid_m = ti.field(dtype=float, shape=grid_tuple)  # grid node interpolated mass
if ti.static(use_antilocking):
    grid_VBar = ti.field(dtype=float, shape=grid_tuple)  # grid node plastic deformation
    grid_JBar = ti.field(dtype=float, shape=grid_tuple)  # grid node volume change ratio
# grid_volume = ti.field(dtype=float, shape=grid_tuple)  # grid node interpolated volume
# grid_J = ti.field(dtype=float, shape=grid_tuple)  # grid node volume change ratio


# ti.root.place(board_states)

#Future class for different complex materials possibly??
#class material_models():
#    def __init__(self) -> None:

def create_parser():
    parser = argparse.ArgumentParser(description="Specify data type for processing.")
    
    parser.add_argument(
        '--data',
        type=str,
        choices=['r', 'rollout', 'test', 't', 'train', 'v', 'valid'],
        required=False,
        help='Specify the type of data: r/rollout/test, t/train, or v/valid'
    )
    
    
    return parser

@ti.func
def update_material_properties(p):
    # Hardening coefficient: snow gets harder when compressed
    # Metals between h = .1-.5
    h = 1.0
    if material[p] == material_id_dict_mpm["Water"]: # 0
        h = 1.0 # No hardening for water
    elif material[p] == material_id_dict_mpm["Plastic"]: # 1 
        h = 1.0 # Do not scale elastic moduli by default
    elif material[p] == material_id_dict_mpm["Debris"]: # 2
        h = 1.0 # Do not scale elastic moduli by default
    elif material[p] == material_id_dict_mpm["Sand"] or material[p] == material_id_dict_mpm["Snow"]: # 3
        # Snow-like material, hardens when compressed, probably from Stoamkhin et al. 2013 or Klar et al.
        h = ti.max(0.1, ti.min(5, ti.exp(10 * (1.0 - Jp[p])))) # Don't calc this unless used, expensive operation
    else:
        h = 1.0 # Do not scale elastic moduli by default

    mu, la = mu_0 * h, lambda_0 * h # adjust elastic moduli based on hardening coefficient

    if material[p] == material_id_dict_mpm["Water"]: # 0:  # liquid 
        mu = 0.0 # assumed no shear modulus... replace with dynamic viscosity later

    return h, mu, la


@ti.func
def neo_hookean_model(h, mu, la, F, J):
    """Due to waters near incompressibility we can treat it as a near-incompressible hyperelastic material

    Args:
        h: Hardening Coefficient
        mu: Shear modulus lame parameter
        la: First Lame parameter (bulk modulus in this context)
        F: Deformation gradient for a single particle
    Returns:

    """
    K = la 
    C = F.transpose() @ F # Left Cauchy Green Tensor
    I1 = C.trace() # Trace of Left Cauchy Green Tensor
    nhstrain = ( mu/2 ) * ( I1 - 3 ) + ( K/2 ) * ( ti.math.log(J)**2 )

    return nhstrain

@ti.func
def compute_stress_svd(p, mu, la):
    """Computing stress using svd 
    Args:
        mu: Shear modulus lame parameter
        la: First Lame parameter (bulk modulus in this context)
        p: Current particle index

    Returns:
        stress: Cauchy stress tensor
    """

    U, sig, V = ti.svd(F[p]) # Singular Value Decomposition of deformation gradient (on particle)

    #J = 1.0
    for d in ti.static(range(DIMENSIONS)):
        new_sig = sig[d, d]
        if material[p] == material_id_dict_mpm["Snow"] or material[p] == material_id_dict_mpm["Sand"]: # 2:  # Snow-like material
            # https://math.ucdavis.edu/~jteran/papers/SSCTS13.pdf
            stomakhin_critical_compression = 2.5e-2
            stomakhin_critical_stretch = 4.5e-3
            new_sig = min(max(sig[d, d], 1 - stomakhin_critical_compression), 1 + stomakhin_critical_stretch)  # Plasticity
        Jp[p] *= sig[d, d] / new_sig # stable?
        sig[d, d] = new_sig
        J *= new_sig

    if material[p] == material_id_dict_mpm["Debris"]: # 2:
        # Reconstruct elastic deformation gradient after plasticity
        F[p] = U @ sig @ V.transpose() # Singular value decomposition, good for large deformations on fixed-corotated model 
    
        stress = 2 * mu * (F[p] - U @ V.transpose()) @ F[p].transpose() + ti.Matrix.identity(float, DIMENSIONS) * la * J * (
            J - 1
        )
        return stress


@ti.func
def compute_stress(mu, la, F):
    """Computes Cauchy stress tensor for a near-incompressible Neo-Hookean material 
    (can also use strain energy density function)

    Args:
        mu: Shear modulus lame parameter
        la: First Lame parameter (bulk modulus in this context)
        F: Deformation gradient for a single particle

    Returns:
        stress: Cauchy stress tensor
    """
    J = ti.math.determinant(F)
    K = la
    FinvT = F.inverse().transpose()
    
    # First Piola-Kirchhoff stress tensor (P)
    P = mu * (F - FinvT) + K * ti.math.log(J) * FinvT

    # Neo-hookean strain energy density function
    # nh_strain = neo_hookean_model( h, mu, la, F, J ) 
    
    # Cauchy stress tensor (denoted by sigma)
    stress = (1 / J) * P @ F.transpose()
    
    return stress


@ti.func
def compute_stress_jfluid(mu, la, J):
    """Computes Cauchy stress tensor for an isotropic fluid material 
    assuming Tait-Murnaghan equation of state 

    Args:
        mu: Shear modulus lame parameter
        la: First Lame parameter (bulk modulus in this context)
        J: Deformation gradient determinant for a single particle

    Returns:
        stress: Cauchy stress tensor
    """
    # J = ti.math.determinant(F[p])  #particle volume ratio = V /Vo

    # pressure = (bulk_modulus / gamma_water ) * (J - 1)
    pressure = (bulk_modulus / gamma_water ) * (ti.pow(J,-gamma_water) - 1) 
    return -pressure * ti.Matrix.identity(float, DIMENSIONS)

@ti.kernel
def substep():
    clear_grid()
    p2g()
    update_grid()
    g2p()
    # handle_piston_collisions()

@ti.func
def clear_grid():
    if ti.static(DIMENSIONS == 2):
        for i, j in grid_m:
            grid_v[i, j] = ti.Vector.zero(float, DIMENSIONS)
            grid_m[i, j] = 0
            if ti.static(use_antilocking):
                grid_VBar[i, j] = 0
                grid_JBar[i, j] = 0
    elif ti.static(DIMENSIONS == 3):
        for i, j, k in grid_m:
            grid_v[i, j, k] = ti.Vector.zero(float, DIMENSIONS)
            grid_m[i, j, k] = 0
            if ti.static(use_antilocking):
                grid_VBar[i, j, k] = 0
                grid_JBar[i, j, k] = 0
    else:
        raise Exception("Improper Dimensionality for Simulation Must Be 2D or 3D ")

@ti.func
def p2g():
    for p in x:  # Particle state update and scatter to grid (P2G)
        base = (x[p] * inv_dx - 0.5).cast(int)
        fx = x[p] * inv_dx - base.cast(float)
        # Quadratic kernels  [http://mpm.graphics   Eqn. 123, with x=fx, fx-1,fx-2] or Weights for MPM
        w = [0.5 * (1.5 - fx) ** 2, 0.75 - (fx - 1) ** 2, 0.5 * (fx - 0.5) ** 2]
        # deformation gradient update
        J_prev = ti.math.determinant(F[p])
        F[p] = (ti.Matrix.identity(float, DIMENSIONS) + dt * C[p]) @ F[p]
        J = ti.math.determinant(F[p])  #particle volume ratio = V /Vo
        
        # Hardening coefficient and Lame parameter updates
        h, mu, la = update_material_properties(p)

        # J=1 undeformed material; J<1 compressed material; J>1 expanded material
        
        
        # Volumetric antilocking / pressure-smoothing via averaging volume deformation on the grid
        # i.e., F-Bar style approach of Zhao et al. 2023 with some modifications if needed, e.g. PA-JB Fluid opt. model,
        # See Bonus 2023 dissertation "Evaluation of Fluid-Driven Debris Impacts in a High-Performance Multi-GPU Material Point Method", I believe Chp 5

        # if ti.static(DIMENSIONS == 2):
        #     JMix = (1.0 - JB_fluid_ratio) * (J) + (JB_fluid_ratio) * JBar[p] * (J / J_prev) 
        # elif ti.static(DIMENSIONS == 3):
        #     JMix = (1.0 - JB_fluid_ratio) * (J) + (JB_fluid_ratio) * JBar[p] * (J / J_prev)       
        
        # Might be a memory-race in taichi, as we read/write to JBar[p] without explicit synchronization
        JMix = J
        if ti.static(use_antilocking):
            JMix = (1.0 - JB_fluid_ratio) * (J) + (JB_fluid_ratio) * JBar[p] * (J / J_prev)   
            # JBar[p] = JMix
        # JBar[p]  
        # F[p] = F[p] * JMix
        
        # Reset deformation gradient to avoid numerical instability
        # if material[p] == material_id_dict_mpm["Water"]: # 0
        #     if DIMENSIONS == 2:
        #         F[p] = ti.Matrix.identity(float, DIMENSIONS) * ti.sqrt(J)
        #     elif DIMENSIONS == 3:
        #         F[p] = ti.Matrix.identity(float, DIMENSIONS) * ti.pow(J, 1/3)
                

        #Neo-hookean formulation for Cauchy stress from first Piola-Kirchoff stress
        stress = ti.Matrix.zero(float, DIMENSIONS, DIMENSIONS)
        if material[p] == material_id_dict_mpm["Water"]:
            if ti.static(use_antilocking):
                stress = compute_stress_jfluid(mu, la, JMix)
            else:
                stress = compute_stress_jfluid(mu, la, J)
        else:
            stress = compute_stress(mu, la, F[p])
                
            
        stress = (-dt * p_vol * 4 * inv_dx * inv_dx) * stress 
        affine = stress + p_mass * C[p]
        if ti.static(DIMENSIONS == 2):
            for i, j in ti.static(ti.ndrange(3, 3)):
                # Loop over 3x3 grid node neighborhood
                offset = ti.Vector([i, j])
                dpos = (offset.cast(float) - fx) * dx
                weight = w[i][0] * w[j][1]
                grid_v[base + offset] += weight * (p_mass * v[p] + affine @ dpos)
                grid_m[base + offset] += weight * p_mass
                if ti.static(use_antilocking):
                    grid_VBar[base + offset] += weight * p_vol
                    grid_JBar[base + offset] += weight * p_vol * JMix
        elif ti.static(DIMENSIONS == 3):
            for i, j, k in ti.static(ti.ndrange(3, 3, 3)):
                offset = ti.Vector([i, j, k])
                dpos = (offset.cast(float) - fx) * dx
                weight = w[i][0] * w[j][1] * w[k][2]
                grid_v[base + offset] += weight * (p_mass * v[p] + affine @ dpos)
                grid_m[base + offset] += weight * p_mass
                if ti.static(use_antilocking):
                    grid_VBar[base + offset] += weight * p_vol
                    grid_JBar[base + offset] += weight * p_vol * JMix

@ti.func
def update_grid():
    if ti.static(DIMENSIONS == 2):
        for i, j in grid_m:
            if grid_m[i, j] > 0:  # No need for epsilon here
                # Momentum to velocity
                grid_v[i, j] = (1 / grid_m[i, j]) * grid_v[i, j]
                grid_v[i, j] += dt * gravity[None]   # gravity
                apply_boundary_conditions(i, j, 0)
    elif ti.static(DIMENSIONS == 3):
        for i, j, k in grid_m:
            if grid_m[i, j, k] > 0: # No need for epsilon here
                # Momentum to velocity
                grid_v[i, j, k] = (1 / grid_m[i, j, k]) * grid_v[i, j, k]
                grid_v[i, j, k] += dt * gravity[None] # gravity
                apply_boundary_conditions(i, j, k)
    
    if ti.static(use_antilocking):
        if ti.static(DIMENSIONS == 2):
            for i, j in grid_VBar:
                if grid_VBar[i, j] > 0:  # No need for epsilon here
                    # Momentum to velocity
                    grid_JBar[i, j] = (1 / grid_VBar[i, j]) * grid_JBar[i, j]
                    apply_boundary_conditions(i, j, 0)
        elif ti.static(DIMENSIONS == 3):
            for i, j, k in grid_VBar:
                if grid_VBar[i, j, k] > 0:
                    grid_JBar[i, j, k] = (1 / grid_VBar[i, j, k]) * grid_JBar[i, j, k]
        

@ti.func
def apply_boundary_conditions(i, j, k):
    
    if ti.static(DIMENSIONS == 2):
        if i < buffer_cells and grid_v[i, j][0] < 0:
            grid_v[i, j][0] = 0  # Boundary conditions
        if i > n_grid_x - buffer_cells and grid_v[i, j][0] > 0:
            grid_v[i, j][0] = 0
        if j < buffer_cells and grid_v[i, j][1] < 0:
            grid_v[i, j][1] = 0
        if j > n_grid_y - buffer_cells and grid_v[i, j][1] > 0:
            grid_v[i, j][1] = 0
        if i <= board_states[None][0] / grid_length * n_grid and grid_v[i, j][0] < board_velocity[None][0]:
            grid_v[i, j][0] = 1.0 * board_velocity[None][0]
    elif ti.static(DIMENSIONS == 3):
        if i < buffer_cells and grid_v[i, j, k][0] < 0:
            grid_v[i, j, k][0] = 0
        if i > n_grid_x - buffer_cells and grid_v[i, j, k][0] > 0:
            grid_v[i, j, k][0] = 0
        if j < buffer_cells and grid_v[i, j, k][1] < 0:
            grid_v[i, j, k][1] = 0
        if j > n_grid_y - buffer_cells and grid_v[i, j, k][1] > 0:
            grid_v[i, j, k][1] = 0
        if k < buffer_cells and grid_v[i, j, k][2] < 0:
            grid_v[i, j, k][2] = 0
        if k > n_grid_z - buffer_cells and grid_v[i, j, k][2] > 0:
            grid_v[i, j, k][2] = 0
        if i > flume_length / grid_length * n_grid - buffer_cells and grid_v[i, j, k][0] > 0:
            grid_v[i, j, k][0] = 0
        if j > flume_height / grid_length * n_grid - buffer_cells and grid_v[i, j, k][1] > 0:
            grid_v[i, j, k][1] = 0
        if k > flume_width / grid_length * n_grid - buffer_cells and grid_v[i, j, k][2] > 0:
            grid_v[i, j, k][2] = 0
        if i <= board_states[None][0] / grid_length * n_grid + 1 and grid_v[i, j, k][0] < board_velocity[None][0]:
            grid_v[i, j, k][0] = 1.0 * board_velocity[None][0]
            
            
        # Everything below must be for bathymetry ramp boundary conditions ONLY

        # ---=== Bathymetry Ramps ===---
        
        # If below the ramp then remove the particle
        
        decay_layer_cells = 2.0 # Thickness of the decay layer, in cells
        decay_layer_inv = 1.0 / decay_layer_cells # Inverse of the decay layer thickness, in cells
        decay_coefficient = 0.0
        velocity_ramp_mag = 0.0

        apply_ramp_condition = False
        velocity_pointing_into_ramp = False
        ramp_normal = ti.Vector([0.0, 0.0, 0.0])
        
        for joint_id in ti.static(range(NUM_RAMPS)):
            in_range_x = (i >= bathymetry_joints_taichi_x[None][joint_id] / grid_length * n_grid) and (i < bathymetry_joints_taichi_x[None][joint_id + 1] / grid_length * n_grid )
            in_range_y = (j <= bathymetry_joints_taichi_y[None][joint_id] / grid_length * n_grid + decay_layer_cells) or (j <= bathymetry_joints_taichi_y[None][joint_id + 1] / grid_length * n_grid + decay_layer_cells)

            bathymetry_slope = (bathymetry_joints_taichi_y[None][joint_id + 1] - bathymetry_joints_taichi_y[None][joint_id]) / (bathymetry_joints_taichi_x[None][joint_id + 1] - bathymetry_joints_taichi_x[None][joint_id])
            j_ramp = bathymetry_slope * (i - (bathymetry_joints_taichi_x[None][joint_id]) / grid_length * n_grid) + (bathymetry_joints_taichi_y[None][joint_id] / grid_length * n_grid)
            below_ramp_surface = (j <= j_ramp)
            below_ramp_decay = (j <= (j_ramp + decay_layer_cells))

            is_a_ramp_cell = (in_range_x and in_range_y) and (below_ramp_decay or below_ramp_surface)
            apply_ramp_condition |= is_a_ramp_cell
            
            # if not is_a_ramp_cell:
            #     continue
            if (is_a_ramp_cell):
                ramp_normal[0] = float(-bathymetry_joints_taichi_y[None][joint_id+1] + bathymetry_joints_taichi_y[None][joint_id])
                ramp_normal[1] = float( bathymetry_joints_taichi_x[None][joint_id+1] - bathymetry_joints_taichi_x[None][joint_id])
                ramp_normal /= ramp_normal.norm()

                # Decay relative the node distance from the ramp surface (linear, quad, tanh, etc). Linear below
                # Allows a smoother transition from the ramp to the rest of the domain, i.e. reduce stair-stepping
                decay_coefficient = ti.min(1.0, ti.max(0.0, float(decay_layer_cells - (j - j_ramp)) * decay_layer_inv))
                

                velocity_ramp_mag = grid_v[i, j, k].dot(ramp_normal)
                velocity_pointing_into_ramp |= (velocity_ramp_mag < 0.0)
            
            
        # Just considering the x and y (streamwise and vertical) components for OSU LWF ramps
        if (apply_ramp_condition and velocity_pointing_into_ramp):
            grid_v[i, j, k] -= decay_coefficient * (velocity_ramp_mag * ramp_normal)
            # grid_v[i, j, k][2] += float(apply_ramp_condition) * decay_coefficient * (velocity_ramp_mag * ramp_normal[2])

    
        
    # piston_pos_current = board_states[None][0]

    # piston_time_stdev = (piston_scale_factor) * 0.707106781187 # (SF / 100) / sqrt(2)
    # piston_time_variance = piston_time_stdev*piston_time_stdev
    # t = time - piston_wait_time
    # piston_pos_current = ti.max(4*dx, ti.min(0.5 * piston_amplitude * ((erf_approx(((t - piston_time_mean)) ) + 1.0) ), 4*dx + piston_amplitude))
    # piston_vel_current = (piston_amplitude / piston_time_stdev) * (ti.exp(-((time - piston_wait_time - piston_time_mean)/(piston_time_stdev*1.41421356237))*((time-piston_wait_time-piston_time_mean)/(piston_time_stdev * 1.41421356237))/2)) * 0.398942280401
    # if (verbose == True):
        # print("Piston Velocity: ", piston_vel_current)
    # print("Time: ", time)
    # print("Piston Position: ", piston_pos_current)
    # print("Piston init Position: ", piston_start_x)
    # print("Piston Velocity: ", piston_vel_current)


@ti.func
def g2p():
    for p in x:  # grid to particle (G2P)
        base = (x[p] * inv_dx - 0.5).cast(int)
        fx = x[p] * inv_dx - base.cast(float)
        w = [0.5 * (1.5 - fx) ** 2, 0.75 - (fx - 1.0) ** 2, 0.5 * (fx - 0.5) ** 2]
        new_v = ti.Vector.zero(float, DIMENSIONS)
        new_C = ti.Matrix.zero(float, DIMENSIONS, DIMENSIONS)
        new_JBar = 0.0
        if ti.static(DIMENSIONS == 2):
            for i, j in ti.static(ti.ndrange(3, 3)):
                # loop over 3x3 grid node neighborhood
                dpos = ti.Vector([i, j]).cast(float) - fx
                g_v = grid_v[base + ti.Vector([i, j])]
                weight = w[i][0] * w[j][1]
                new_v += weight * g_v
                new_C += 4 * inv_dx * weight * g_v.outer_product(dpos)
                if ti.static(use_antilocking):
                    g_JBar = grid_JBar[base + ti.Vector([i, j])]
                    new_JBar += weight * grid_JBar[base + ti.Vector([i, j])]
        elif ti.static(DIMENSIONS == 3):
            for i, j, k in ti.static(ti.ndrange(3, 3, 3)):
                # loop over 3x3x3 grid node neighborhood
                dpos = ti.Vector([i, j, k]).cast(float) - fx
                g_v = grid_v[base + ti.Vector([i, j, k])]
                weight = w[i][0] * w[j][1] * w[k][2]
                new_v += weight * g_v
                new_C += 4 * inv_dx * weight * g_v.outer_product(dpos)
                if ti.static(use_antilocking):
                    g_JBar = grid_JBar[base + ti.Vector([i, j, k])]
                    new_JBar += weight * grid_JBar[base + ti.Vector([i, j, k])]

        v[p], C[p] = new_v, new_C
        x[p] += dt * v[p]  # advection
        if ti.static(use_antilocking):
            JBar[p] = new_JBar


# @ti.func
# def handle_piston_collisions():
#     for p in x:
#         # Apply piston force based on Hooke's law
#         # piston_pos_current = board_states[None][0]
#         piston_pos_current = ti.max(4*dx, ti.min(0.5 * piston_amplitude * ((erf_approx(((time - piston_time_mean)) ) + 1.0) ), 4*dx + piston_amplitude))
#         if x[p][0] < piston_pos_current:
#             # Using separable contact, i.e. water doesn't stick if not being pushed
#             displacement_into_piston = ti.max(piston_pos_current - x[p][0], 0.0)
#             piston_spring_constant = p_mass / dt  # Assume a 1.0 kg mass 
#             force = ti.max(piston_spring_constant * displacement_into_piston, 0.0)  # Hooke's law: F = k * x
#             piston_escape_velocity = 1 * force / p_mass * dt  # v = F / m * dt
#             piston_escape_velocity = ti.min(piston_escape_velocity, max_vel)  # Cap the velocity to prevent instability
#             v[p][0] = ti.max(v[p][0], piston_escape_velocity)  # Stop the particle from moving into the piston

# @ti.func
def erf_approx(erf_x):
    """Needed an approximation to the gauss error function (math lib doesnt work with taichi)
    
    From: https://en.wikipedia.org/wiki/Error_function
    """
    # Approximation constants
    a1 =  0.254829592
    a2 = -0.284496736
    a3 =  1.421413741
    a4 = -1.453152027
    a5 =  1.061405429
    erf_p  =  0.3275911

    # Save the sign of x
    sign = 1
    if erf_x < 0:
        sign = -1
    erf_x = abs(erf_x)

    # Abramowitz and Stegun formula 
    erf_t = 1.0 / (1.0 + erf_p * erf_x)
    erf_y = 1.0 - (((((a5 * erf_t + a4) * erf_t) + a3) * erf_t + a2) * erf_t + a1) * erf_t * math.exp(-erf_x * erf_x)

    return sign * erf_y


def move_board_solitary():
    t = time - piston_wait_time 
    b = board_states[None]
    bv = board_velocity[None]
    # b[1] += dt  # Adjusting for the coordinate frame

    # if b[1] >= 2 * piston_period:
    #     b[1] = 0


    piston_time_variance = piston_time_stdev * piston_time_stdev
    b[0] = 0.5 * piston_amplitude * ((math.erf((t - piston_time_mean) / (piston_time_stdev) * 0.707106781187 ) + 1.0) ) + piston_pos[0]  #Soliton wave
    # b[0] = 0.5 * piston_amplitude * ((erf_approx((t - piston_time_mean - 1e-2)) + 1.0) ) + 4 * dx  #Soliton wave
    bv[0] = (piston_amplitude / piston_time_stdev) * (math.exp(-((t - piston_time_mean)/(piston_time_stdev))*((t - piston_time_mean)/(piston_time_stdev)) * 0.5)) * 0.398942280401
    
    # b[0] += 0.
    # bv[0] += 0.
    # print("Piston Velocity: ", piston_vel_current)
    # Ensure the piston stays within the boundaries
    # b[0] = ti.max(0.0, ti.min(b[0], piston_pos[0] + piston_amplitude))
    
    # Store the updated state back to the field
    board_states[None] = b
    board_velocity[None] = bv


@ti.kernel
def reset():
    for i in range(n_particles):
        if ti.static(DIMENSIONS == 2):
            v[i] = [0.0, 0.0]
            F[i] = ti.Matrix([[1.0, 0.0], [0.0, 1.0]])
            Jp[i] = 1.0
            C[i] = ti.Matrix.zero(float, DIMENSIONS, DIMENSIONS)
            if ti.static(use_antilocking):
                JBar[i] = 1.0
        elif ti.static(DIMENSIONS == 3):
            v[i] = [0.0, 0.0, 0.0]
            F[i] = ti.Matrix([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
            Jp[i] = 1.0
            C[i] = ti.Matrix.zero(float, DIMENSIONS, DIMENSIONS)
        
            
            if ti.static(use_antilocking):
                JBar[i] = 1.0
    
    if ti.static(DIMENSIONS == 2):
        for i, j in grid_m:
            grid_m[i, j] = 0
            grid_v[i, j] = [0.0, 0.0]
            if ti.static(use_antilocking):
                grid_VBar[i, j] = 0
                grid_JBar[i, j] = 0
    elif ti.static(DIMENSIONS == 3):
        for i, j, k in grid_m:
            grid_m[i, j, k] = 0
            grid_v[i, j, k] = [0.0, 0.0, 0.0]
            if ti.static(use_antilocking):
                grid_VBar[i, j, k] = 0
                grid_JBar[i, j, k] = 0
                
        
        
    if ti.static(DIMENSIONS == 2):
        board_states[None] = [float(piston_pos[0]), 0.0]  # Initial piston position
        board_velocity[None] = [0.0, 0.0]  # Initial piston velocity
    elif ti.static(DIMENSIONS == 3):
        board_states[None] = [float(piston_pos[0]), 0.0, 0.0]  # Initial piston position
        board_velocity[None] = [0.0, 0.0, 0.0]  # Initial piston velocity
     

def save_metadata(file_path):
    """Save metadata.json to file
    Args:
        file_path: the path to save the metadata (**Automatically retrieved by system**)
        bounds: The boundaries for plotting the animated simulation
        sequence_length: The number of time steps taken
        DIMENSIONS: The dimensionality of the simulation (i.e. 2d or 3d)
        time_delta: Defined as 1 / frames per second of the simulation (typically 20-30 fps)
        dx: Change in grid sizing defined as the grid_length / # of Grids
        dt: Time rate of change using CFL for simulation stability (dt = CFL * dx / max_vel)

    ** NOTE: GNS currently does not have use for variables "critical_time_step": dt or "dx": dx. **
       
       Returns:
        None
    """
    #Using a list for each time step for formatting
    global v_data_to_save
    # Assume scaled already
    global bounds
    vel = np.stack(v_data_to_save,axis=0) / grid_length # Scale velocity data to 1x1x1 domain for GNS
    vel_diff = np.diff(vel, axis=0) #computing acceleration along the time dependant axis
    
    #Define meta data dictionary from trajectories and timesteps
    if DIMENSIONS == 2:
        vel_mean = np.nanmean(vel, axis=(0))
        vel_std = np.nanstd(vel, axis=(0))
        acc_mean = np.nanmean(vel_diff, axis=(0))
        acc_std = np.nanstd(vel_diff, axis=(0))
    elif DIMENSIONS == 3:
        vel_mean = np.nanmean(vel, axis=(0, 1))
        vel_std = np.nanstd(vel, axis=(0, 1))
        acc_mean = np.nanmean(vel_diff, axis=(0, 1))
        acc_std = np.nanstd(vel_diff, axis=(0, 1))
    else:
        raise Exception("Improper Dimensionality for Simulation Must Be 2D or 3D ")    
    
    # Convert numpy types to native Python types
    vel_mean = [float(x) for x in vel_mean]
    vel_std = [float(x) for x in vel_std]
    acc_mean = [float(x) for x in acc_mean]
    acc_std = [float(x) for x in acc_std]
    
    # Rough estimate of the bspline interaction range between initial particles
    connectivity_amplifier = 1.5
    bspline_max_reach = 1.5 # [Cell Units]
    downsampled_connectivity_radius = (2 * dx * connectivity_amplifier * bspline_max_reach) / (downsampling_ratio**(1/DIMENSIONS))
    downsampled_connectivity_radius = float(downsampled_connectivity_radius / grid_length) # Scale to 1x1x1 domain for GNS
    bounds_i = []
    bounds_temp = []
    bounds_metadata = []
    for i in range(DIMENSIONS):
        bounds_i = [float(bound_ii) for bound_ii in bounds[i]]
        bounds_metadata.append(bounds_i)
    
    for i in range(DIMENSIONS):
        while (bounds_metadata[i][0] < 0.0 or bounds_metadata[i][0] > 1.0):
            print("Scale bounds to 0 to 1 range")
            bounds_metadata[i][0] = float(abs(bounds_metadata[i][0]) / grid_length)
            
        while (bounds_metadata[i][1] > 1.0 or bounds_metadata[i][1] < 0.0):    
            print("Scale bounds to 0 to 1 range")
            bounds_metadata[i][1] = float(abs(bounds_metadata[i][1]) / grid_length)
        
        if (bounds_metadata[i][0] == bounds_metadata[i][1]):
            print("Assume that the bounds were centered around the origin, so we shift the to the right into 0 to 1 range")
            bounds_metadata[i][0] = 0.5 - min(0.5, bounds_metadata[i][0])
            bounds_metadata[i][1] = 0.5 + min(0.5, bounds_metadata[i][1])
            
        if (bounds_metadata[i][0] > bounds_metadata[i][1]):
            print("Swap the bounds if they are in the wrong order, i.e. [1, 0] -> [0, 1]")
            bounds_temp = float(bounds_metadata[i][0])
            bounds_metadata[i][0] = float(bounds_metadata[i][1])
            # bounds = bounds_temp
           
    
    # Formatting enforced
    metadata = {
        "bounds": bounds_metadata,
        "sequence_length": sequence_length, 
        "default_connectivity_radius": downsampled_connectivity_radius, 
        "dim": DIMENSIONS, 
        "dt": 1.0 / fps, 
        "dx": dx,
        "critical_time_step": dt,
        "vel_mean": vel_mean, #[5.123277536458455e-06, -0.0009965205918140803], 
        "vel_std": vel_std, #[0.0021978993231675805, 0.0026653552458701774], 
        "acc_mean": acc_mean, #[5.237611158734309e-07, 2.3633027988858656e-07], 
        "acc_std": acc_std, #[0.0002582944917306106, 0.00029554531667679154]
    }
    
    print("Constricted simulation metadata: ", metadata)
    
    # Write metadata to a JSON file
    with open(os.path.join(file_path, 'metadata.json'), 'w') as file:
        json.dump(metadata, file)
        print("Saved metadata to file: ", os.path.join(file_path, 'metadata.json', "\n"))

def save_simulation():
    """Save train.npz, test.npz,or valid.npz to file
    Args:
        None
    Returns:
        None
    """
    global data_designation
    global data

    # Define file_path to save to data, models, rollout folder. Located in directory of this file script

    if system == 'linux':
        file_path = "./Flume/dataset"
    elif system == 'darwin':  # 'Darwin' is the system name for macOS
        file_path = "Flume/dataset"
        #file_path = "/Users/" + user_name + "/code-REU/Physics-Informed-ML/dataset/"
    elif system == 'windows':
        file_path = "./Flume/dataset"
    else:
        file_path = "./Flume/dataset"

    save_relative_to_cwd = True
    ROLLOUT_PATH="./Flume/rollout"
    MODEL_PATH="./Flume/models"
    if save_relative_to_cwd:
         cwd_path = os.getcwd()
         file_path = os.path.join(cwd_path, file_path)
         ROLLOUT_PATH = os.path.join(cwd_path, ROLLOUT_PATH)
         MODEL_PATH = os.path.join(cwd_path, MODEL_PATH)

    # Ensuring the directories exist within the cwd
    if not os.path.exists(file_path):
        os.makedirs(file_path)
    if not os.path.exists(ROLLOUT_PATH):
        os.makedirs(ROLLOUT_PATH)
    if not os.path.exists(MODEL_PATH):
        os.makedirs(MODEL_PATH)


    material_numpy = material.to_numpy()
    mat_data_tmp = np.where(material_numpy == material_id_dict_mpm["Water"], material_id_dict_gns["Water"] + (0 * material_numpy), material_numpy)

    mat_data = np.asarray(mat_data_tmp, dtype=object)
    pos_data = np.stack(data_to_save, axis=0) / grid_length # Scale position data to 1x1x1 domain for GNS

    print("Down-sampling index list: ", idx_sampled.shape)


    # Perform downsampling for GNS
    downsampling_style = "poisson-disk"
    if downsampling_style == "poisson-disk":
        # Use the idx's to sample the data (i.e. according to Poisson-Disk Sampling, voxel, manual, etv)
        downsampled_data = pos_data[:, idx_sampled, :]
        downsampled_mat_data = mat_data[idx_sampled]
    elif downsampling_style == "stride":
        # Save a GNS downsampled particle every downampling_ratio
        downsampled_data = pos_data[:,::downsampling_ratio,:]
        downsampled_mat_data = mat_data[::downsampling_ratio]
    elif downsampling_style == "random":
        # Randomly sample the data
        downsampled_data = pos_data[:, np.random.choice(pos_data.shape[1], size=xyz_sampled.size[0], replace=False), :]
        downsampled_mat_data = mat_data[np.random.choice(mat_data.shape[0], size=xyz_sampled.size[0], replace=False)]
    else:
        raise Exception("Downsampling Style Not Supported")

    #check version of numpy >= 1.22.0
    # Newer versions of numpy require the dtype to be explicitly set to object, I think, for some python versions
    # Should add a check for the python version as well
    
    if (np.version.version > '1.23.5'):
        print("Using numpy version (>= 1.23.5), may require alternative approach to save npz files (e.g. dtype=object): ", np.version.version)
        pos_data = np.array(np.stack(np.asarray(downsampled_data, dtype=object), axis=0), dtype=object)
        mat_data = np.asarray(downsampled_mat_data, dtype=object)
    else:
        print("Warning: Using numpy version: ", np.version.version)
        pos_data = np.array(np.stack(downsampled_data, axis=0), dtype=np.float32)
        mat_data = np.asarray(downsampled_mat_data, dtype=object)
        # np.array(material_data.tolist()) 
    
    print("pos_data: ", pos_data.shape)
    print("mat_data: ", mat_data.shape)
    simulation_data = {
        'simulation_0': (
            pos_data,
            mat_data
        )
    }
        
    if data_designation.lower() in ("r", "rollout", "test"):
        # Should clarify the difference in naming between test and rollout
        print("Saving simulatiomn particle data to: [", file_path, "/test.npz] for testing of surrogate models trained on similar but different data, i.e. [train.npz].")
        output_file_path = os.path.join(file_path, "test.npz")
        np.savez_compressed(f'{file_path}/test.npz', **simulation_data)
        output_particles_as_vtk = True


    elif data_designation.lower() in ("t", "train"):
        print("Saving simulation particle data to: [", file_path, "/train.npz] for training surrogate models.")
        output_file_path = os.path.join(file_path, "train.npz")
        np.savez_compressed(f'{file_path}/train.npz', **simulation_data)
        save_metadata(file_path)

    elif data_designation.lower() in ("v", "valid"):
        print("Saving simulation particle data to: [", file_path, "/valid.npz] for validation of trained and tested surrogate models.")
        output_file_path = os.path.join(file_path, "valid.npz")
        np.savez_compressed(f'{file_path}/valid.npz', **simulation_data) # Proper 
        
    else:
        "Unspecified data designation, saving to: [",  cwd_path,"/unspecified_sim_data.npz]"
        output_file_path = os.path.join(cwd_path, "unspecified_sim_data.npz")
        np.savez_compressed(os.path.join(cwd_path, "unspecified_sim_data2.npz"), **simulation_data)
        #np.savez_compressed('simulation_data_kwargs.npz', pos_data=downsampled_data, material_ids=downsampled_mat_data)
        # Save to HDF5
        #with h5py.File(f'{cwd_path}/unspecified_sim_data.h5', 'w') as f:
        #    f.create_dataset('pos_data', data=downsampled_data)
        #    f.create_dataset('material_ids', data=downsampled_mat_data)
    print("Simulation Data Saved to: ", file_path)


parser = create_parser()
args = parser.parse_args()
if args.data is None:
    data_designation = str(input('What is the output particle data for? Select: Rollout(R), Training(T), Valid(V) [Waiting for user input...] --> ')).lower()
else:
    data_designation = args.data
if data_designation in ("r", "rollout", "test"):
    data_designation = 'Rollout'
elif data_designation in ("v", "valid"):
    data_designation = 'Valid'
elif data_designation in ("t", "train"):
    data_designation = 'Train'

# sequence_length = int(input('How many time steps to simulate? --> ')) 
fps = 60
sequence_length = 10 * fps # May want to provide an FPS input 

# Preallocate numpy arrays to store particle positions and velocities
# NOTE: This can become many GBs large, exceeding the RAM of your computer. TODO: Use a file(s) on disk and perform writes in smaller chunks from the RAM
x_data_gns= np.zeros((sequence_length, n_particles, DIMENSIONS), dtype=np.float32) # float 32 for mac compatibility
v_data_gns = np.zeros((sequence_length, n_particles, DIMENSIONS), dtype=np.float32)
#wave_numerical_soln = np.zeros((sequence_length, 3), dtype=np.float32) # 1. time 2. corresponding x positional value 3. max y 
max_wave_y = -np.inf # Initialize with unmistakable minamal value
max_wave_ind = 0
wave_water_condition = (material.to_numpy()[:] == material_id_dict_mpm["Water"]) # Boolean Conditional for water
wave_formed = False
wave_height = 0
formed_wave_frames = 0
time_formed = 0.0  # Reset time once wave is fully formed

palette = [0x2389da, 0xED553B, 0x068587, 0x6D214F]
gravity[None] = [0.0, -9.80665, 0.0] # Gravity in m/s^2, this implies use of metric units

# Saving Figures of the simulation (2D only so far)
base_frame_dir = './Flume/figures/'
os.makedirs(base_frame_dir, exist_ok=True) # Ensure the directory exists
frame_paths = []

def T(a):
    if dim == 2:
        return a

    phi, theta = np.radians(60), np.radians(32)

    a = a - 0.5
    x, y, z = a[:, 0], a[:, 1], a[:, 2]
    cp, sp = np.cos(phi), np.sin(phi)
    ct, st = np.cos(theta), np.sin(theta)
    x, z = x * cp + z * sp, z * cp - x * sp
    u, v = x, y * ct + z * st
    return np.array([u, v]).swapaxes(0, 1) + 0.5



reset() # Reset sim and initialize particles


for frame in range(sequence_length):
    # for s in range(int(2e-3 // dt)): # Will need to double-check the use of 2e-3, dt, etc.
    for s in range(int((1.0/fps) // dt)): 
        move_board_solitary()
        substep()
        time += dt # Update time by dt so that the time used in move_board_solitary() is accurate, otherwise the piston moves only once every frame position-wise which causes instabilities


    # 30 is a magic number, make it a parameter
    
    print("\n" + "=" * 30)
    print("     Simulation Details     ")
    print(f"     ({str(data_designation.capitalize())})     ")
    print("=" * 30)
    if wave_height_expected - 0.2 <= wave_height <= wave_height_expected + 0.1:
        print(f"Frame: {frame}, Saving Frame: {formed_wave_frames}")
        print(f"Time: {time:.3f}, Time of formed wave: {time_formed:.3f}")
    else:
        print(f"Frame: {frame}")
        print(f"Time: {time:.3f}")

    print("-" * 30)

    print("=" * 30)
    print("  Simulation Particle Data  ")
    print("=" * 30)

    if board_states[None][0] < piston_wait_time:  # max piston draw is 3.9m
        print(f"Piston Position x = {board_states[None][0]:.5f}")
        if board_velocity[None][0] >= 0.2: # .2 as an arbitrary velocity (10ish percent) to start displaying Piston Velocity
            print(f"Piston Velocity V_x = {board_velocity[None][0]:.5f}")
    
    x_np = x.to_numpy()
    data_to_save.append(x.to_numpy()) # Save particle positions for each substep
    v_data_to_save.append(v.to_numpy()) # Save particle velocities for each substep
    
    # Get max y for the wave
    if time > piston_wait_time:  # Start getting wave data after the initial wave has started
        if x_np.size > 0:  # Ensuring x has values
            max_wave_ind = np.argmax(x_np[:, 1][wave_water_condition])  # Index of max wave value
            max_wave_y = x_np[max_wave_ind, 1]  # Max water particle Value for each time step based on boolean indexing
            wave_height = max_wave_y - max_base_y

            print(f"\nCurrent Wave Height: {wave_height:.3f}(m)")
            print(f"Expected Wave Height: {wave_height_expected:.3f}(m)")

            if wave_height_expected - 0.2 <= wave_height <= wave_height_expected + 0.1:
                if not wave_formed:
                    wave_formed = True
                    #wave_numerical_soln = np.zeros((abs(sequence_length-frame), 3), dtype=np.float32)
                    print("\n" + "*" * 30)
                    print("Wave is fully formed.")
                    #print("Starting to save wave data.")
                    print("*" * 30)

                if wave_formed:
                    time_formed += dt
                    #wave_numerical_soln[formed_wave_frames, 0] = time_formed  # Time 
                    #wave_numerical_soln[formed_wave_frames, 1] = x_np[max_wave_ind, 0]  # Save x position of max water particle value
                    #wave_numerical_soln[formed_wave_frames, 2] = wave_height  # Wave Amplitude
                    formed_wave_frames += 1
                #print("\n...Saving Wave Data...")

            else:
                print("\nWave height not within expected range.")

    print("\n" + "=" * 30) 
    
    clipped_material = np.clip(material.to_numpy(), 0, len(palette) - 1) #handles error where the number of materials is greater len(palette)

    vis_velocity_magnitude = True
    vis_wave_elevation = False
    vis_pressure = True
    Elevation_palette = []
    Velocity_palette = []
    Pressure_palette = []
    if vis_wave_elevation:
        Elevation_vmin = max_water_depth_tsunami + 1*wave_height_expected - dx * (buffer_cells + buffer_shift_particles) + particle_spacing / 2
        Elevation_vmax = 3*wave_height_expected
        Elevation_img = (cm.hsv(0.5 + np.maximum(-1/3.0, (x.to_numpy()[:, 1] - Elevation_vmin) / (Elevation_vmax) ))[:,:3] * 255).astype(np.uint8)
        Elevation_palette = [ (int('0x' + ''.join(f'{rgb_component:02X}' for rgb_component in rgba_tuple[:3]), 0)) for rgba_tuple in Elevation_img] 
    if vis_velocity_magnitude:
        Velocity_vmax = 1.0
        if (experiment == "breaking"):
            Velocity_vmax = 4.0
        elif (experiment == "unbreaking"):
            Velocity_vmax = 0.25
        Velocity_img = ((cm.viridis(np.sqrt(v.to_numpy()[:n_particles_water, 0]**2 + v.to_numpy()[:n_particles_water, 1]**2 + v.to_numpy()[:n_particles_water,2]**2) / Velocity_vmax)[:,:3]) * 255).astype(np.uint8)
        Velocity_img = np.concatenate((Velocity_img, ((cm.magma(np.sqrt(v.to_numpy()[n_particles_water:(n_particles_water+n_particles_debris_group), 0]**2 + v.to_numpy()[n_particles_water:(n_particles_water+n_particles_debris_group), 1]**2 + v.to_numpy()[n_particles_water:(n_particles_water+n_particles_debris_group),2]**2) / Velocity_vmax)[:,:3]) * 255).astype(np.uint8)), axis=0)
        Velocity_palette = [ (int('0x' + ''.join(f'{rgb_component:02X}' for rgb_component in rgba_tuple[:3]), 0)) for rgba_tuple in Velocity_img] 

    if vis_pressure:
        Pressure_vmax = (max_water_depth_tsunami + wave_height_expected)* 9.80665 * .10000
        Pressure_img = (cm.turbo(np.maximum(0.0,(bulk_modulus / gamma_water) * (np.linalg.det(F.to_numpy()[:])**(-gamma_water) - 1) / (Pressure_vmax)))[:,:3] * 255).astype(np.uint8)
        Pressure_palette = [ (int('0x' + ''.join(f'{rgb_component:02X}' for rgb_component in rgba_tuple[:3]), 0)) for rgba_tuple in Pressure_img]    
    palette_questions = [vis_velocity_magnitude, vis_wave_elevation, vis_pressure] # Order matters
    palette_options = [Velocity_palette, Elevation_palette, Pressure_palette]

    requested_a_palette = False # Default to false, check if any palette was requested below
    j = 0
    for answer in palette_questions:
        if answer:
            # Take first selected palette and remove it from the remaining options       
            requested_a_palette = palette_questions.pop(j) # Remove the first question that was answered true
            chosen_palette = palette_options.pop(j) # Retrieve palette and remove from remaining options
            chosen_palette_indices = [palette_idx for palette_idx in range(len(chosen_palette))] # Indices for above palette
            break
        j += 1
    if not requested_a_palette:
        # If no palette was requested, default to the original material segmented palette
        chosen_palette = palette
        chosen_palette_indices = clipped_material
    
    np_x = x.to_numpy()
    
                
    frame_filename = f'frame_{frame:05d}.png'
    frame_path = os.path.join(base_frame_dir, frame_filename)

    if output_particles_as_vtk:
        # Save the particles as a VTK file
        # x_sf.from_numpy(np_x.reshape(-1, 3))
        
        
        # ti.tools.vtk.write_vtk(x_sf, f'particles_frame_{frame:05d}') # NOTE: vtk saves a *.vtr file
        pointsToVTK(f'particles_frame_{frame:05d}',
                    np.array(np_x[:,0], dtype=np.float32),  
                    np.array(np_x[:,1], dtype=np.float32), 
                    np.array(np_x[:,2], dtype=np.float32), 
                    data={"elev": np.array(np_x[:,1],dtype=int)},
                    )#, "id": material.to_numpy(), "pressure": Jp.to_numpy()}) #, "velocity": np.linalg.norm(v.to_numpy().reshape(-1, 3), axis=1)})

        # # Example 2
        # x = np.arange(1.0, 10.0, 0.1)
        # y = np.arange(1.0, 10.0, 0.1)
        # z = np.arange(1.0, 10.0, 0.1)
        # pointsToVTK("./line_points", np_x[:][0], np_x[:][1], np_x[:][2], data={"elev": np_x[:][1]})

    if output_png and not output_gui:
        try:
            tools.imwrite(x.to_numpy(), frame_path)
            frame_paths.append(frame_path)
        except Exception as e:
            print(f"Error writing frame: {e}")
    else:
        print("WARNING - No output method selected, frame not saved or displayed...")
    if not output_gui:
        continue

# Check if there are frames to create a GIF
if frame_paths:
    gif_path = f"./Flume/simulation_{n_particles}.gif"
    try:
        with imageio.get_writer(gif_path, mode='I', duration=0.1) as writer:
            for frame_path in frame_paths:
                image = imageio.imread(frame_path)
                writer.append_data(image)
        print(f"GIF created at {gif_path}")
    except Exception as e:
        print(f"Error creating GIF: {e}")
    

# TODO: Keep wave formed, wave time saving, and saving_frme in the main loop.
# TODO: Delete data_to_save and v_data_to_save 
# TODO: Save it all to either taichi or csv file
# TODO: Save Max water particle values to wave_numerical_soln = np.zeros((abs(formed_wave_frames), 3), dtype=np.float32)


# Prep for GNS input
save_simulation()


#if using save_sim.py script
#ss.save_sim_data(data_designation, data_to_save, v_data_to_save, material, 
 #                bounds, sequence_length, DIMENSIONS, time_delta, dx, dt)