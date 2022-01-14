import warnings

import numpy as np
from numba import jit
from numba.typed import List
from pycce.sm import _smc


def rotmatrix(initial_vector, final_vector):
    r"""
    Generate 3D rotation matrix which applied on initial vector will produce vector, aligned with final vector.

    Examples:

        >>> R = rotmatrix([0,0,1], [1,1,1])
        >>> R @ np.array([0,0,1])
        array([0.577, 0.577, 0.577])

    Args:
        initial_vector (ndarray with shape(3, )): Initial vector.
        final_vector (ndarray with shape (3, )): Final vector.

    Returns:
        ndarray with shape (3, 3): Rotation matrix.
    """

    iv = np.asarray(initial_vector)
    fv = np.asarray(final_vector)
    a = iv / np.linalg.norm(iv)
    b = fv / np.linalg.norm(fv)  # Final vector

    c = a @ b  # Cosine between vectors
    # if they're antiparallel
    if c == -1.:
        raise ValueError('Vectors are antiparallel')

    v = np.cross(a, b)
    screw_v = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]])
    r = np.eye(3) + screw_v + np.dot(screw_v, screw_v) / (1 + c)

    return r


@jit(nopython=True)
def expand(matrix, i, dim):
    """
    Expand matrix M from it's own dimensions to the total Hilbert space.

    Args:
        matrix (ndarray with shape (dim[i], dim[i])): Inital matrix.
        i (int): Index of the spin dimensions in ``dim`` parameter.
        dim (ndarray): Array pf dimensions of all spins present in the cluster.

    Returns:
        ndarray with shape (prod(dim), prod(dim)): Expanded matrix.
    """
    dbefore = dim[:i].prod()
    dafter = dim[i + 1:].prod()

    expanded_matrix = np.kron(np.kron(np.eye(dbefore, dtype=np.complex128), matrix),
                              np.eye(dafter, dtype=np.complex128))

    return expanded_matrix


def dimensions_spinvectors(bath=None, central_spin=None):
    """
    Generate two arrays, containing dimensions of the spins in the cluster and the vectors with spin matrices.

    Args:
        bath (BathArray with shape (n,)): Array of the n spins within cluster.
        central_spin (CenterArray, optional): If provided, include dimensions of the central spins.

    Returns:
        tuple: *tuple* containing:

            * **ndarray with shape (n,)**: Array with dimensions for each spin.

            * **list**: List with vectors of spin matrices for each spin in the cluster
              (Including central spin if ``central_spin`` is not None). Each with  shape (3, N, N) where
              ``N = prod(dimensions)``.
    """
    dimensions = []

    if bath is not None:
        dimensions += [n.dim for n in bath]

    if central_spin is not None:
        try:
            for c in central_spin:
                dimensions += [c.dim]

        except TypeError:
            dimensions += [central_spin.dim]

    dimensions = np.array(dimensions, dtype=np.int32)
    vectors = vecs_from_dims(dimensions)

    return dimensions, vectors


@jit(cache=True, nopython=True)
def vecs_from_dims(dimensions):
    td = dimensions.prod()
    vectors = np.zeros((len(dimensions), 3, td, td), dtype=np.complex128)
    for j, d in enumerate(dimensions):
        vectors[j] = spinvec(j, dimensions)
    return vectors


@jit(nopython=True)
def spinvec(j, dimensions):
    x, y, z = _gen_sm(dimensions[j])
    vec = np.stack((expand(x, j, dimensions),
                    expand(y, j, dimensions),
                    expand(z, j, dimensions))
                   )
    return vec


def generate_projections(state_a, state_b=None, spins=None):
    r"""
    Generate vector with the spin projections of the given spin states:

    .. math::

        [\bra{a}\hat{S}_x\ket{b}, \bra{a}\hat{S}_y\ket{b}, \bra{a}\hat{S}_z\ket{b}],

    where :math:`\ket{a}` and :math:`\ket{b}` are the given spin states.

    Args:
        state_a (ndarray): state `a` of the central spin in :math:`\hat{S}_z` basis.
        state_b (ndarray): state `b` of the central spin in :math:`\hat{S}_z` basis.

    Returns:
        ndarray with shape (3,): :math:`[\braket{\hat{S}_x}, \braket{\hat{S}_y}, \braket{\hat{S}_z}]` projections.
    """
    if state_b is None:
        state_b = state_a
    if spins is None:
        spin = (state_a.size - 1) / 2
        sm = _smc[spin]

        projections = np.array([state_a.conj() @ sm.x @ state_b,
                                state_a.conj() @ sm.y @ state_b,
                                state_a.conj() @ sm.z @ state_b],
                               dtype=np.complex128)
    else:
        projections = []
        dim = (np.asarray(spins) * 2 + 1 + 1e-8).astype(int)

        for i, s in enumerate(spins):
            sm = _smc[s]
            smx = expand(sm.x, i, dim)
            smy = expand(sm.y, i, dim)
            smz = expand(sm.z, i, dim)

            p = np.array([state_a.conj() @ smx @ state_b,
                          state_a.conj() @ smy @ state_b,
                          state_a.conj() @ smz @ state_b],
                         dtype=np.complex128)
            projections.append(p)

    return projections


def zfs_tensor(D, E=0):
    """
    Generate (3, 3) ZFS tensor from observable parameters D and E.

    Args:
        D (float or ndarray with shape (3, 3)): Longitudinal splitting (D) in ZFS **OR** total ZFS tensor.
        E (float): Transverse splitting (E) in ZFS.

    Returns:
        ndarray with shape (3, 3): Total ZFS tensor.
    """
    if isinstance(D, (np.floating, float, int)):

        tensor = np.zeros((3, 3), dtype=np.float64)
        tensor[2, 2] = 2 / 3 * D
        tensor[1, 1] = -D / 3 - E
        tensor[0, 0] = -D / 3 + E
    else:
        tensor = D
    return tensor


@jit(nopython=True)
def _gen_sm(dim):
    """
    Numba-friendly spin matrix.
    Args:
        dim (int): dimensions of the spin marix.

    Returns:
        ndarray:
    """
    s = (dim - 1) / 2
    projections = np.linspace(-s, s, dim).astype(np.complex128)
    plus = np.zeros((dim, dim), dtype=np.complex128)

    for i in range(dim - 1):
        plus[i, i + 1] += np.sqrt(s * (s + 1) -
                                  projections[i] * projections[i + 1])

    minus = plus.conj().T
    x = 1 / 2. * (plus + minus)
    y = 1 / 2j * (plus - minus)
    z = np.diag(projections[::-1])
    return x, y, z


def partial_inner_product(avec, total, dimensions, index=-1):
    r"""
    Returns partial inner product :math:`\ket{b}=\bra{a}\ket{\psi}`, where :math:`\ket{a}` provided by
    ``avec`` contains degrees of freedom to be "traced out" and :math:`\ket{\psi}` provided by ``total``
    is the total statevector.

    Args:
        avec (ndarray with shape (a,)):
        total (ndarray with shape (a*b,)):
        dimensions (ndarray with shape (n,)):
        index ():

    Returns:

    """
    if len(total.shape) == 1:
        matrix = np.moveaxis(total.reshape(dimensions), index, -1)
        matrix = matrix.reshape([np.prod(np.delete(dimensions, index)), dimensions[index]])
    else:
        total = total.reshape(total.shape[0], *dimensions)
        matrix = np.moveaxis(total, index, -1)
        matrix = matrix.reshape([total.shape[0], np.prod(np.delete(dimensions, index)), dimensions[index]])
    return avec @ matrix


@jit(nopython=True)
def shorten_dimensions(dimensions, central_number):
    if central_number > 1:
        shortdims = dimensions[:-central_number + 1].copy()
        # reduced dimension so all central spin dimensions are gathered in one
        shortdims[-1] = np.prod(dimensions[-central_number:])
    else:
        shortdims = dimensions
    return shortdims


@jit(nopython=True)
def gen_state_list(states, dims):
    list_of_vectors = List()
    for s, d in zip(states, dims):
        list_of_vectors.append(vector_from_s(s, d))
    return list_of_vectors


@jit(nopython=True)
def vector_from_s(s, d):
    vec_nucleus = np.zeros(d, dtype=np.complex128)
    state_number = np.int32((d - 1) / 2 - s)
    vec_nucleus[state_number] = 1
    return vec_nucleus


@jit(nopython=True)
def from_central_state(dimensions, central_state):
    return expand(central_state, len(dimensions) - 1, dimensions) / dimensions[:-1].prod()


@jit(nopython=True)
def from_none(dimensions):
    tdim = np.prod(dimensions)
    return np.eye(tdim) / tdim


@jit(nopython=True)
def from_states(states):
    cluster_state = states[0]
    for s in states[1:]:
        cluster_state = np.kron(cluster_state, s)

    return cluster_state


def combine_cluster_central(cluster_state, central_state):
    lcs = len(cluster_state.shape)
    ls = len(central_state.shape)

    if lcs != ls:
        return noneq_cc(cluster_state, central_state)
    else:
        return eq_cc(cluster_state, central_state)


@jit(nopython=True)
def noneq_cc(cluster_state, central_state):
    if len(cluster_state.shape) == 1:
        matrix = outer(cluster_state, cluster_state)
        return np.kron(matrix, central_state)

    else:
        matrix = outer(central_state, central_state)
        return np.kron(cluster_state, matrix)


@jit(nopython=True)
def eq_cc(cluster_state, central_state):
    return np.kron(cluster_state, central_state)


@jit(nopython=True)
def rand_state(d):
    return np.eye(d, dtype=np.complex128) / d


@jit(nopython=True)
def outer(s1, s2):
    return np.outer(s1, s2.conj())


def generate_initial_state(dimensions, states=None, central_state=None):
    if states is None:
        if central_state is None:
            return from_none(dimensions)
        else:
            if len(central_state.shape) == 1:
                central_state = outer(central_state, central_state)
            return from_central_state(dimensions, central_state)

    has_none = not states.has_state.all()
    all_pure = False
    all_mixed = False

    if not has_none:
        all_pure = states.pure.all()
        if not all_pure:
            all_mixed = (~states.pure).all()

    if has_none:
        for i in range(states.size):
            if states[i] is None:
                states[i] = rand_state(dimensions[i])

    if not (all_pure or all_mixed):
        for i in range(states.size):

            if len(states[i].shape) < 2:
                states[i] = outer(states[i], states[i])

    cluster_state = from_states(list(states))

    if central_state is not None:
        cluster_state = combine_cluster_central(cluster_state, central_state)

    return cluster_state


@jit(nopython=True)
def tensor_vdot(tensor, ivec):
    result = np.zeros((tensor.shape[1], *ivec.shape[1:]), dtype=ivec.dtype)
    for i, row in enumerate(tensor):
        for j, a_ij in enumerate(row):
            result[i] += a_ij * ivec[j]
    return result


@jit(nopython=True)
def vvdot(vec_1, vec_2):
    result = np.zeros(vec_1.shape[1:], vec_1.dtype)
    for v1, v2 in zip(vec_1, vec_2):
        result += v1 @ v2
    return result


def rotate_tensor(tensor, rotation=None, style='col'):
    if rotation is None:
        return tensor
    if style.lower == 'row':
        rotation = rotation.T
    if np.isclose(np.linalg.inv(rotation), rotation.T, rtol=1e-04).all():
        invrot = rotation.T
    else:
        warnings.warn(f"Rotation {rotation} changes distances. Is that desired behavior?", stacklevel=2)
        invrot = np.linalg.inv(rotation)
    tensor_rotation = np.matmul(tensor, rotation)
    res = np.matmul(invrot, tensor_rotation)
    #  Suppress very small deviations
    res[np.isclose(res, 0)] = 0
    return (res + np.swapaxes(res, -1, -2)) / 2


def rotate_coordinates(xyz, rotation=None, cell=None, style='col'):
    if style.lower() == 'row':
        if rotation is not None:
            rotation = rotation.T
        if cell is not None:
            cell = cell.T
    if cell is not None:
        xyz = np.einsum('jk,...k->...j', cell, xyz)
    if rotation is not None:
        if np.isclose(np.linalg.inv(rotation), rotation.T).all():
            invrot = rotation.T
        else:
            warnings.warn(f"Rotation {rotation} changes distances. Is that desired behavior?", stacklevel=2)
            invrot = np.linalg.inv(rotation)

        xyz = np.einsum('jk,...k->...j', invrot, xyz)
    #  Suppress very small deviations
    xyz[np.isclose(xyz, 0)] = 0

    return xyz


def normalize(vec):
    vec = np.asarray(vec, dtype=np.complex128)
    return vec / np.linalg.norm(vec)
