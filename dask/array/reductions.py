from __future__ import absolute_import, division, print_function

import numpy as np
from functools import partial, wraps
from toolz import compose, curry

from .core import (_concatenate2, insert_many, Array, atop, names, sqrt,
        elemwise)
from ..core import flatten


def reduction(x, chunk, aggregate, axis=None, keepdims=None):
    """ General version of reductions

    >>> reduction(my_array, np.sum, np.sum, axis=0, keepdims=False)  # doctest: +SKIP
    """
    if axis is None:
        axis = tuple(range(x.ndim))
    if isinstance(axis, int):
        axis = (axis,)

    chunk2 = partial(chunk, axis=axis, keepdims=True)
    aggregate2 = partial(aggregate, axis=axis, keepdims=keepdims)

    inds = tuple(range(x.ndim))
    tmp = atop(chunk2, next(names), inds, x, inds)

    inds2 = tuple(i for i in inds if i not in axis)

    result = atop(compose(aggregate2, curry(_concatenate2, axes=axis)),
                  next(names), inds2, tmp, inds)

    if keepdims:
        dsk = result.dask.copy()
        for k in flatten(result._keys()):
            k2 = (k[0],) + insert_many(k[1:], axis, 0)
            dsk[k2] = dsk.pop(k)
        blockdims = insert_many(result.blockdims, axis, [1])
        return Array(dsk, result.name, blockdims=blockdims)
    else:
        return result


@wraps(np.sum)
def sum(a, axis=None, keepdims=False):
    return reduction(a, np.sum, np.sum, axis=axis, keepdims=keepdims)


@wraps(np.min)
def min(a, axis=None, keepdims=False):
    return reduction(a, np.min, np.min, axis=axis, keepdims=keepdims)


@wraps(np.max)
def max(a, axis=None, keepdims=False):
    return reduction(a, np.max, np.max, axis=axis, keepdims=keepdims)


@wraps(np.argmin)
def argmin(a, axis=None):
    return arg_reduction(a, np.min, np.argmin, axis=axis)


@wraps(np.argmax)
def argmax(a, axis=None):
    return arg_reduction(a, np.max, np.argmax, axis=axis)


@wraps(np.any)
def any(a, axis=None, keepdims=False):
    return reduction(a, np.any, np.any, axis=axis, keepdims=keepdims)


@wraps(np.all)
def all(a, axis=None, keepdims=False):
    return reduction(a, np.all, np.all, axis=axis, keepdims=keepdims)


@wraps(np.mean)
def mean(a, axis=None, keepdims=False):
    def chunk(x, **kwargs):
        n = np.ones_like(x).sum(**kwargs)
        total = np.sum(x, **kwargs)
        result = np.empty(shape=n.shape,
                  dtype=[('total', total.dtype), ('n', n.dtype)])
        result['n'] = n
        result['total'] = total
        return result
    def agg(pair, **kwargs):
        return pair['total'].sum(**kwargs) / pair['n'].sum(**kwargs)
    return reduction(a, chunk, agg, axis=axis, keepdims=keepdims)


@wraps(np.var)
def var(a, axis=None, keepdims=False, ddof=0):
    def chunk(A, **kwargs):
        n = np.ones_like(A).sum(**kwargs)
        x = np.sum(A, dtype='f8', **kwargs)
        x2 = np.sum(A**2, dtype='f8', **kwargs)
        result = np.empty(shape=n.shape, dtype=[('x', x.dtype),
                                                ('x2', x2.dtype),
                                                ('n', n.dtype)])
        result['x'] = x
        result['x2'] = x2
        result['n'] = n
        return result

    def agg(A, **kwargs):
        x = A['x'].sum(**kwargs)
        x2 = A['x2'].sum(**kwargs)
        n = A['n'].sum(**kwargs)
        result = (x2 / n) - (x / n)**2
        if ddof:
            result = result * n / (n - ddof)
        return result

    return reduction(a, chunk, agg, axis=axis, keepdims=keepdims)


@wraps(np.std)
def std(a, axis=None, keepdims=False, ddof=0):
    return sqrt(a.var(axis=axis, keepdims=keepdims, ddof=ddof))


def vnorm(a, ord=None, axis=None, keepdims=False):
    """ Vector norm

    See np.linalg.norm
    """
    if ord is None or ord == 'fro':
        ord = 2
    if ord == np.inf:
        return max(abs(a), axis=axis, keepdims=keepdims)
    elif ord == -np.inf:
        return min(abs(a), axis=axis, keepdims=keepdims)
    elif ord == 1:
        return sum(abs(a), axis=axis, keepdims=keepdims)
    elif ord % 2 == 0:
        return sum(a**ord, axis=axis, keepdims=keepdims)**(1./ord)
    else:
        return sum(abs(a)**ord, axis=axis, keepdims=keepdims)**(1./ord)


def arg_aggregate(func, argfunc, dims, pairs):
    """

    >>> pairs = [([4, 3, 5], [10, 11, 12]),
    ...          ([3, 5, 1], [1, 2, 3])]
    >>> arg_aggregate(np.min, np.argmin, (100, 100), pairs)
    array([101, 11, 103])
    """
    pairs = list(pairs)
    mins, argmins = zip(*pairs)
    mins = np.array(mins)
    argmins = np.array(argmins)
    args = argfunc(mins, axis=0)

    offsets = np.add.accumulate([0] + list(dims)[:-1])
    offsets = offsets.reshape((len(offsets),) + (1,) * (argmins.ndim - 1))
    return np.choose(args, argmins + offsets)


def arg_reduction(a, func, argfunc, axis=0):
    """ General version of argmin/argmax

    >>> arg_reduction(my_array, np.min, axis=0)  # doctest: +SKIP
    """
    if not isinstance(axis, int):
        raise ValueError("Must specify integer axis= keyword argument.\n"
                "For example:\n"
                "  Before:  x.argmin()\n"
                "  After:   x.argmin(axis=0)\n")

    def argreduce(x):
        """ Get both min/max and argmin/argmax of each block """
        return (func(x, axis=axis), argfunc(x, axis=axis))

    a2 = elemwise(argreduce, a)

    return atop(partial(arg_aggregate, func, argfunc, a.blockdims[axis]),
                next(names), [i for i in range(a.ndim) if i != axis],
                a2, list(range(a.ndim)))