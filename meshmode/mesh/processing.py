from __future__ import division
from __future__ import absolute_import
from six.moves import range
from functools import reduce

__copyright__ = "Copyright (C) 2014 Andreas Kloeckner"

__license__ = """
Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
"""

import numpy as np
import numpy.linalg as la
import modepy as mp


__doc__ = """
.. autofunction:: find_volume_mesh_element_orientations
.. autofunction:: perform_flips
.. autofunction:: find_bounding_box
.. autofunction:: merge_disjoint_meshes
.. autofunction:: affine_map
"""


# {{{ orientations

def find_volume_mesh_element_group_orientation(mesh, grp):
    """Return a positive floating point number for each positively
    oriented element, and a negative floating point number for
    each negatively oriented element.
    """

    from meshmode.mesh import SimplexElementGroup

    if not isinstance(grp, SimplexElementGroup):
        raise NotImplementedError(
                "finding element orientations "
                "only supported on "
                "exclusively SimplexElementGroup-based meshes")

    # (ambient_dim, nelements, nvertices)
    vertices = mesh.vertices[:, grp.vertex_indices]

    # (ambient_dim, nelements, nspan_vectors)
    spanning_vectors = (
            vertices[:, :, 1:] - vertices[:, :, 0][:, :, np.newaxis])

    ambient_dim = spanning_vectors.shape[0]
    nspan_vectors = spanning_vectors.shape[-1]

    spanning_object_array = np.empty(
            (nspan_vectors, ambient_dim),
            dtype=np.object)

    for ispan in range(nspan_vectors):
        for idim in range(ambient_dim):
            spanning_object_array[ispan, idim] = \
                    spanning_vectors[idim, :, ispan]

    from pymbolic.geometric_algebra import MultiVector

    mvs = [MultiVector(vec) for vec in spanning_object_array]

    from operator import xor
    outer_prod = -reduce(xor, mvs)

    return (outer_prod.I | outer_prod).as_scalar()


def find_volume_mesh_element_orientations(mesh, tolerate_unimplemented_checks=False):
    """Return a positive floating point number for each positively
    oriented element, and a negative floating point number for
    each negatively oriented element.

    :arg tolerate_unimplemented_checks: If *True*, elements for which no
        check is available will return NaN.
    """

    result = np.empty(mesh.nelements, dtype=np.float64)

    for grp in mesh.groups:
        result_grp_view = result[
                grp.element_nr_base:grp.element_nr_base + grp.nelements]

        if tolerate_unimplemented_checks:
            try:
                signed_area_elements = \
                        find_volume_mesh_element_group_orientation(mesh, grp)
            except NotImplementedError:
                result_grp_view[:] = float("nan")
            else:
                assert not np.isnan(signed_area_elements).any()
                result_grp_view[:] = signed_area_elements
        else:
            signed_area_elements = \
                    find_volume_mesh_element_group_orientation(mesh, grp)
            assert not np.isnan(signed_area_elements).any()
            result_grp_view[:] = signed_area_elements

    return result


def test_volume_mesh_element_orientations(mesh):
    area_elements = find_volume_mesh_element_orientations(
            mesh, tolerate_unimplemented_checks=True)

    valid = ~np.isnan(area_elements)

    return (area_elements[valid] > 0).all()

# }}}


# {{{ flips

def flip_simplex_element_group(vertices, grp, grp_flip_flags):
    from modepy.tools import barycentric_to_unit, unit_to_barycentric

    from meshmode.mesh import SimplexElementGroup

    if not isinstance(grp, SimplexElementGroup):
        raise NotImplementedError("flips only supported on "
                "exclusively SimplexElementGroup-based meshes")

    # Swap the first two vertices on elements to be flipped.

    new_vertex_indices = grp.vertex_indices.copy()
    new_vertex_indices[grp_flip_flags, 0] \
            = grp.vertex_indices[grp_flip_flags, 1]
    new_vertex_indices[grp_flip_flags, 1] \
            = grp.vertex_indices[grp_flip_flags, 0]

    # Generate a resampling matrix that corresponds to the
    # first two barycentric coordinates being swapped.

    bary_unit_nodes = unit_to_barycentric(grp.unit_nodes)

    flipped_bary_unit_nodes = bary_unit_nodes.copy()
    flipped_bary_unit_nodes[0, :] = bary_unit_nodes[1, :]
    flipped_bary_unit_nodes[1, :] = bary_unit_nodes[0, :]
    flipped_unit_nodes = barycentric_to_unit(flipped_bary_unit_nodes)

    flip_matrix = mp.resampling_matrix(
            mp.simplex_onb(grp.dim, grp.order),
            flipped_unit_nodes, grp.unit_nodes)

    flip_matrix[np.abs(flip_matrix) < 1e-15] = 0

    # Flipping twice should be the identity
    assert la.norm(
            np.dot(flip_matrix, flip_matrix)
            - np.eye(len(flip_matrix))) < 1e-13

    # Apply the flip matrix to the nodes.
    new_nodes = grp.nodes.copy()
    new_nodes[:, grp_flip_flags] = np.einsum(
            "ij,dej->dei",
            flip_matrix, grp.nodes[:, grp_flip_flags])

    return SimplexElementGroup(
            grp.order, new_vertex_indices, new_nodes,
            unit_nodes=grp.unit_nodes)


def perform_flips(mesh, flip_flags, skip_tests=False):
    flip_flags = flip_flags.astype(np.bool)

    from meshmode.mesh import Mesh

    new_groups = []
    for grp in mesh.groups:
        grp_flip_flags = flip_flags[
                grp.element_nr_base:grp.element_nr_base+grp.nelements]

        if grp_flip_flags.any():
            new_grp = flip_simplex_element_group(
                    mesh.vertices, grp, grp_flip_flags)
        else:
            new_grp = grp.copy()

        new_groups.append(new_grp)

    return Mesh(mesh.vertices, new_groups, skip_tests=skip_tests)

# }}}


# {{{ bounding box

def find_bounding_box(mesh):
    """
    :return: a tuple *(min, max)*, each consisting of a :class:`numpy.ndarray`
        indicating the minimal and maximal extent of the geometry along each axis.
    """

    return (
            np.min(mesh.vertices, axis=-1),
            np.max(mesh.vertices, axis=-1),
            )

# }}}


# {{{ merging

def merge_disjoint_meshes(meshes, skip_tests=False):
    if not meshes:
        raise ValueError("must pass at least one mesh")

    from pytools import is_single_valued
    if not is_single_valued(mesh.ambient_dim for mesh in meshes):
        raise ValueError("all meshes must share the same ambient dimension")

    # {{{ assemble combined vertex array

    ambient_dim = meshes[0].ambient_dim
    nvertices = sum(
            mesh.vertices.shape[-1]
            for mesh in meshes)

    vert_dtype = np.find_common_type(
            [mesh.vertices.dtype for mesh in meshes],
            [])
    vertices = np.empty(
            (ambient_dim, nvertices), vert_dtype)

    current_vert_base = 0
    vert_bases = []
    for mesh in meshes:
        mesh_nvert = mesh.vertices.shape[-1]
        vertices[:, current_vert_base:current_vert_base+mesh_nvert] = \
                mesh.vertices

        vert_bases.append(current_vert_base)
        current_vert_base += mesh_nvert

    # }}}

    # {{{ assemble new groups list

    new_groups = []

    for mesh, vert_base in zip(meshes, vert_bases):
        for group in mesh.groups:
            new_vertex_indices = group.vertex_indices + vert_base
            new_group = group.copy(vertex_indices=new_vertex_indices)
            new_groups.append(new_group)

    # }}}

    from meshmode.mesh import Mesh
    return Mesh(vertices, new_groups, skip_tests=skip_tests)

# }}}


# {{{ affine map

def affine_map(mesh, A=None, b=None):  # noqa
    """Apply the affine map *f(x)=Ax+b* to the geometry of *mesh*."""

    if A is None:
        A = np.eye(mesh.ambient_dim)  # noqa

    if b is None:
        b = np.zeros(A.shape[0])

    vertices = np.einsum("ij,jv->iv", A, mesh.vertices) + b[:, np.newaxis]

    # {{{ assemble new groups list

    new_groups = []

    for group in mesh.groups:
        nodes = (
                np.einsum("ij,jen->ien", A, group.nodes)
                + b[:, np.newaxis, np.newaxis])
        new_groups.append(group.copy(nodes=nodes))

    # }}}

    from meshmode.mesh import Mesh
    return Mesh(vertices, new_groups, skip_tests=True,
            element_connectivity=mesh.connectivity_init_arg())

# }}}

# vim: foldmethod=marker
