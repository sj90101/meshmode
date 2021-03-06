from __future__ import division
from __future__ import absolute_import
import six
from six.moves import range
from six.moves import zip

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

from meshpy.gmsh_reader import (  # noqa
        GmshMeshReceiverBase, FileSource, LiteralSource)


__doc__ = """

.. autoclass:: FileSource
.. autoclass:: LiteralSource

.. autofunction:: read_gmsh
.. autofunction:: generate_gmsh

"""


# {{{ gmsh receiver

class GmshMeshReceiver(GmshMeshReceiverBase):
    def __init__(self):
        # Use data fields similar to meshpy.triangle.MeshInfo and
        # meshpy.tet.MeshInfo
        self.points = None
        self.elements = None
        self.element_types = None
        self.element_markers = None
        self.tags = None

    def set_up_nodes(self, count):
        # Preallocate array of nodes within list; treat None as sentinel value.
        # Preallocation not done for performance, but to assign values at indices
        # in random order.
        self.points = [None] * count

    def add_node(self, node_nr, point):
        self.points[node_nr] = point

    def finalize_nodes(self):
        self.points = np.array(self.points, dtype=np.float64)

    def set_up_elements(self, count):
        # Preallocation of arrays for assignment elements in random order.
        self.element_vertices = [None] * count
        self.element_nodes = [None] * count
        self.element_types = [None] * count
        self.element_markers = [None] * count
        self.tags = []

    def add_element(self, element_nr, element_type, vertex_nrs,
            lexicographic_nodes, tag_numbers):
        self.element_vertices[element_nr] = vertex_nrs
        self.element_nodes[element_nr] = lexicographic_nodes
        self.element_types[element_nr] = element_type
        self.element_markers[element_nr] = tag_numbers

    def finalize_elements(self):
        pass

    def add_tag(self, name, index, dimension):
        pass

    def finalize_tags(self):
        pass

    def get_mesh(self):
        el_type_hist = {}
        for el_type in self.element_types:
            el_type_hist[el_type] = el_type_hist.get(el_type, 0) + 1

        groups = self.groups = []

        ambient_dim = self.points.shape[-1]

        mesh_bulk_dim = max(
                el_type.dimensions for el_type in six.iterkeys(el_type_hist))

        # {{{ build vertex numbering

        vertex_index_gmsh_to_mine = {}
        for el_vertices, el_type in zip(
                self.element_vertices, self.element_types):
            for gmsh_vertex_nr in el_vertices:
                if gmsh_vertex_nr not in vertex_index_gmsh_to_mine:
                    vertex_index_gmsh_to_mine[gmsh_vertex_nr] = \
                            len(vertex_index_gmsh_to_mine)

        # }}}

        # {{{ build vertex array

        gmsh_vertex_indices, my_vertex_indices = \
                list(zip(*six.iteritems(vertex_index_gmsh_to_mine)))
        vertices = np.empty(
                (ambient_dim, len(vertex_index_gmsh_to_mine)), dtype=np.float64)
        vertices[:, np.array(my_vertex_indices, np.intp)] = \
                self.points[np.array(gmsh_vertex_indices, np.intp)].T

        # }}}

        from meshmode.mesh import SimplexElementGroup, Mesh

        for group_el_type, ngroup_elements in six.iteritems(el_type_hist):
            if group_el_type.dimensions != mesh_bulk_dim:
                continue

            nodes = np.empty((ambient_dim, ngroup_elements, el_type.node_count()),
                    np.float64)
            el_vertex_count = group_el_type.vertex_count()
            vertex_indices = np.empty(
                    (ngroup_elements, el_vertex_count),
                    np.int32)
            i = 0

            for el_vertices, el_nodes, el_type in zip(
                    self.element_vertices, self.element_nodes, self.element_types):
                if el_type is not group_el_type:
                    continue

                nodes[:, i] = self.points[el_nodes].T
                vertex_indices[i] = [vertex_index_gmsh_to_mine[v_nr]
                        for v_nr in el_vertices]

                i += 1

            unit_nodes = (np.array(group_el_type.lexicographic_node_tuples(),
                    dtype=np.float64).T/group_el_type.order)*2 - 1

            group = SimplexElementGroup(
                group_el_type.order,
                vertex_indices,
                nodes,
                unit_nodes=unit_nodes
                )

            # Gmsh seems to produce elements in the opposite orientation
            # of what we like. Flip them all.

            if group.dim == 2:
                from meshmode.mesh.processing import flip_simplex_element_group
                group = flip_simplex_element_group(vertices, group,
                        np.ones(ngroup_elements, np.bool))

            groups.append(group)

        return Mesh(vertices, groups, element_connectivity=None)

# }}}


def read_gmsh(filename, force_ambient_dim=None):
    """Read a gmsh mesh file from *filename* and return a
    :class:`meshmode.mesh.Mesh`.

    :arg force_ambient_dim: if not None, truncate point coordinates to
        this many dimensions.
    """
    from meshpy.gmsh_reader import read_gmsh
    recv = GmshMeshReceiver()
    read_gmsh(recv, filename, force_dimension=force_ambient_dim)

    return recv.get_mesh()


def generate_gmsh(source, dimensions, order=None, other_options=[],
        extension="geo", gmsh_executable="gmsh", force_ambient_dim=None):
    """Run :command:`gmsh` on the input given by *source*, and return a
    :class:`meshmode.mesh.Mesh` based on the result.

    :arg source: an instance of either :class:`FileSource` or
        :class:`LiteralSource`
    :arg force_ambient_dim: if not None, truncate point coordinates to
        this many dimensions.
    """
    recv = GmshMeshReceiver()

    from meshpy.gmsh import GmshRunner
    from meshpy.gmsh_reader import parse_gmsh
    with GmshRunner(source, dimensions, order=order,
            other_options=other_options, extension=extension,
            gmsh_executable=gmsh_executable) as runner:
        parse_gmsh(recv, runner.output_file,
                force_dimension=force_ambient_dim)

    mesh = recv.get_mesh()

    if force_ambient_dim is None:
        AXIS_NAMES = "xyz"  # noqa

        dim = mesh.vertices.shape[0]
        for idim in range(dim):
            if (mesh.vertices[idim] == 0).all():
                from warnings import warn
                warn("all vertices' %s coordinate is zero--perhaps you want to pass "
                        "force_ambient_dim=%d (pass any fixed value to "
                        "force_ambient_dim to silence this warning)" % (
                            AXIS_NAMES[idim], idim))
                break

    return mesh
