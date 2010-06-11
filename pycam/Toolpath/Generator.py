# -*- coding: utf-8 -*-
"""
$Id$

Copyright 2010 Lars Kruse <devel@sumpfralle.de>

This file is part of PyCAM.

PyCAM is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

PyCAM is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with PyCAM.  If not, see <http://www.gnu.org/licenses/>.
"""

from pycam.PathGenerators import DropCutter, PushCutter, EngraveCutter
import pycam.PathProcessors
import pycam.Cutters
import pycam.Toolpath.SupportGrid
import pycam.Geometry.Model
import sys

DIRECTIONS = frozenset(("x", "y", "xy"))
PATH_GENERATORS = frozenset(("DropCutter", "PushCutter", "EngraveCutter"))
PATH_POSTPROCESSORS = frozenset(("ContourCutter", "PathAccumulator", "PolygonCutter", "SimpleCutter", "ZigZagCutter"))
CALCULATION_BACKENDS = frozenset((None, "ODE"))

def generate_toolpath(model, tool_settings=None,
        bounds=None, direction="x", path_generator="DropCutter",
        path_postprocessor="ZigZagCutter", material_allowance=0.0,
        safety_height=None, overlap=0.0, step_down=0.0,
        support_grid_distance=None, support_grid_thickness=None,
        calculation_backend=None, callback=None):
    """ abstract interface for generating a toolpath

    @type model: pycam.Geometry.Model.Model
    @value model: a model contains surface triangles or a contour
    @type tool_settings: dict
    @value tool_settings: contains at least the following keys (depending on
        the tool type):
        "shape": any of possible cutter shape (see "pycam.Cutters")
        "radius": main radius of the tools
        "torus_radius": (only for ToroidalCutter) second toroidal radius
    @type bounds: tuple(float) | list(float)
    @value bounds: the processing boundary (relative to the center of the tool)
        (order: minx, maxx, miny, maxy, minz, maxz)
    @type direction: str
    @value direction: any member of the DIRECTIONS set (e.g. "x", "y" or "xy")
    @type path_generator: str
    @value path_generator: any member of the PATH_GENERATORS set
    @type path_postprocessor: str
    @value path_postprocessor: any member of the PATH_POSTPROCESSORS set
    @type material_allowance: float
    @value material_allowance: the minimum distance between the tool and the model
    @type overlap: float
    @value overlap: the overlap between two adjacent tool paths (0 <= overlap < 1)
    @type step_down: float
    @value step_down: maximum height of each layer (for PushCutter)
    @type support_grid_distance: float
    @value support_grid_distance: grid size of remaining support material
    @type support_grid_thickness: float
    @value support_grid_thickness: thickness of the support grid
    @type calculation_backend: str | None
    @value calculation_backend: any member of the CALCULATION_BACKENDS set
        The default is the triangular collision detection.
    @rtype: pycam.Toolpath.ToolPath | str
    @return: the resulting toolpath object or an error string in case of invalid
        arguments
    """
    if bounds is None:
        # no bounds were given - we use the boundaries of the model
        minx, maxx = model.minx, model.maxx
        miny, maxy = model.miny, model.maxy
        minz, maxz = model.minz, model.maxz
    else:
        minx, maxx, miny, maxy, minz, maxz = bounds
    # trimesh model or contour model?
    if isinstance(model, pycam.Geometry.Model.Model):
        trimesh_model = model
        contour_model = None
    else:
        trimesh_model = pycam.Geometry.Model.Model()
        contour_model = model
    # create the grid model if requested
    if (not support_grid_distance is None) \
            and (not support_grid_thickness is None):
        if support_grid_distance <= 0:
            return "The distance of the support grid must be a positive value"
        if support_grid_thickness <= 0:
            return "The thickness of the support grid must be a positive value"
        support_grid_model = pycam.Toolpath.SupportGrid.get_support_grid(
                minx, maxx, miny, maxy, minz, support_grid_distance,
                support_grid_distance, support_grid_thickness)
        trimesh_model += support_grid_model
    # Due to some weirdness the height of the drill must be bigger than the object's size.
    # Otherwise some collisions are not detected.
    cutter_height = 4 * (maxy - miny)
    cutter = pycam.Cutters.get_tool_from_settings(tool_settings, cutter_height)
    if isinstance(cutter, basestring):
        return cutter
    cutter.set_required_distance(material_allowance)
    physics = _get_physics(trimesh_model, cutter, calculation_backend)
    if isinstance(physics, basestring):
        return physics
    generator = _get_pathgenerator_instance(trimesh_model, contour_model, cutter, path_generator, path_postprocessor, material_allowance, safety_height, physics)
    if isinstance(generator, basestring):
        return generator
    if (overlap < 0) or (overlap >= 1):
        return "Invalid overlap value (%f): should be greater or equal 0 and lower than 1"
    effective_toolradius = tool_settings["radius"] * (1.0 - overlap)
    if path_generator == "DropCutter":
        if direction == "x":
            direction_param = 0
        elif direction == "y":
            direction_param = 1
        else:
            return "Invalid direction value (%s): not one of %s" % (direction, DIRECTIONS)
        if safety_height < maxz:
            return "Safety height (%.4f) is within the bounding box height (%.4f) - this can cause collisions of the tool with the material." % (safety_height, maxz)
        toolpath = generator.GenerateToolPath(minx, maxx, miny, maxy, minz, maxz,
                effective_toolradius, effective_toolradius, direction_param, callback)
    elif path_generator == "PushCutter":
        if step_down > 0:
            dz = step_down
        else:
            dz = maxz - minz
        if direction == "x":
            dx, dy = 0, effective_toolradius
        elif direction == "y":
            dx, dy = effective_toolradius, 0
        elif direction == "xy":
            dx, dy = effective_toolradius, effective_toolradius
        else:
            return "Invalid direction (%s): not one of %s" % (direction, DIRECTIONS)
        toolpath = generator.GenerateToolPath(minx, maxx, miny, maxy, minz, maxz, dx, dy, dz, callback)
    else:
        # EngraveCutter
        if step_down > 0:
            dz = step_down
        else:
            dz = maxz - minz
        toolpath = generator.GenerateToolPath(minz, maxz, effective_toolradius, dz, callback)
    return toolpath
    
def _get_pathgenerator_instance(trimesh_model, contour_model, cutter, pathgenerator, pathprocessor,
        material_allowance, safety_height, physics):
    if pathgenerator == "DropCutter":
        if pathprocessor == "ZigZagCutter":
            processor = pycam.PathProcessors.PathAccumulator(zigzag=True)
        elif pathprocessor == "PathAccumulator":
            processor = pycam.PathProcessors.PathAccumulator()
        else:
            return "Invalid postprocessor (%s) for 'DropCutter': only 'ZigZagCutter' or 'PathAccumulator' are allowed" % str(pathprocessor)
        return DropCutter.DropCutter(cutter, trimesh_model, processor, physics=physics,
                safety_height=safety_height)
    elif pathgenerator == "PushCutter":
        if pathprocessor == "PathAccumulator":
            processor = pycam.PathProcessors.PathAccumulator()
        elif pathprocessor == "SimpleCutter":
            processor = pycam.PathProcessors.SimpleCutter()
        elif pathprocessor == "ZigZagCutter":
            processor = pycam.PathProcessors.ZigZagCutter()
        elif pathprocessor == "PolygonCutter":
            processor = pycam.PathProcessors.PolygonCutter()
        elif pathprocessor == "ContourCutter":
            processor = pycam.PathProcessors.ContourCutter()
        else:
            return "Invalid postprocessor (%s) for 'PushCutter' - it should be one of these: %s" % (processor, PATH_POSTPROCESSORS)
        return PushCutter.PushCutter(cutter, trimesh_model, processor, physics=physics)
    elif pathgenerator == "EngraveCutter":
        if pathprocessor == "SimpleCutter":
            processor = pycam.PathProcessors.SimpleCutter()
        else:
            return "Invalid postprocessor (%s) for 'EngraveCutter' - it should be one of these: %s" % (processor, PATH_POSTPROCESSORS)
        if not contour_model:
            return "The EngraveCutter requires a contour model (e.g. from a DXF file)."
        return EngraveCutter.EngraveCutter(cutter, trimesh_model, contour_model, processor, physics=physics)
    else:
        return "Invalid path generator (%s): not one of %s" % (pathgenerator, PATH_GENERATORS)

def _get_physics(trimesh_model, cutter, calculation_backend):
    if calculation_backend is None:
        # triangular collision detection does not need any physical model
        return None
    elif calculation_backend == "ODE":
        import pycam.Physics.ode_physics
        return pycam.Physics.ode_physics.generate_physics(trimesh_model, cutter)
    else:
        return "Invalid calculation backend (%s): not one of %s" % (calculation_backend, CALCULATION_BACKENDS)
