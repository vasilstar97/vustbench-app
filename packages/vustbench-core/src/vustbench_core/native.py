"""A local compatibility adapter for ObjectNat 3.1.0 sparse matrix access.

ObjectNat correctly recognizes infinity-filled pandas sparse tables, but then
calls ``DataFrame.sparse.to_coo``. Pandas only supports a zero fill value for
that accessor. This subclass supplies the finite-entry COO representation that
ObjectNat expects, preserving absent=infinity and explicit reachable zeroes.
Neither pandas nor ObjectNat is monkeypatched and no dense matrix is created.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import sparse


def finite_coo(matrix: pd.DataFrame) -> sparse.coo_matrix:
    row_chunks: list[np.ndarray] = []
    column_chunks: list[np.ndarray] = []
    value_chunks: list[np.ndarray] = []
    for column, (_, series) in enumerate(matrix.items()):
        array = series.array
        if isinstance(array, pd.arrays.SparseArray):
            if not (pd.isna(array.fill_value) or np.isinf(array.fill_value)):
                raise ValueError("Routing sparse tables must use infinity/NaN for absent pairs")
            positions = array.sp_index.to_int_index().indices
            values = np.asarray(array.sp_values, dtype=np.float32)
        else:
            values = np.asarray(series, dtype=np.float32)
            positions = np.arange(len(series), dtype=np.int32)
        finite = np.isfinite(values)
        row_chunks.append(positions[finite])
        column_chunks.append(np.full(int(finite.sum()), column, dtype=np.int32))
        value_chunks.append(values[finite])
    rows = np.concatenate(row_chunks) if row_chunks else np.array([], dtype=np.int32)
    columns = np.concatenate(column_chunks) if column_chunks else np.array([], dtype=np.int32)
    values = np.concatenate(value_chunks) if value_chunks else np.array([], dtype=np.float32)
    return sparse.coo_matrix((values, (rows, columns)), shape=matrix.shape)


class _FiniteSparseAccessor:
    def __init__(self, frame: pd.DataFrame) -> None:
        self.frame = frame

    def to_coo(self) -> sparse.coo_matrix:
        return finite_coo(self.frame)


class ObjectNatDistanceFrame(pd.DataFrame):
    @property
    def _constructor(self):
        return ObjectNatDistanceFrame

    @property
    def sparse(self) -> _FiniteSparseAccessor:
        return _FiniteSparseAccessor(self)
