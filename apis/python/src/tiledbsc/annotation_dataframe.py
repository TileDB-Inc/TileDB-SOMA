import tiledb
from .tiledb_array import TileDBArray
from .tiledb_group import TileDBGroup
from .soma_options import SOMAOptions
import tiledbsc.util as util

import pandas as pd
import numpy as np

from typing import Optional, Tuple, List


class AnnotationDataFrame(TileDBArray):
    """
    Nominally for obs and var data within a soma. These have one string dimension, and multiple attributes.
    """

    dim_name: str

    # ----------------------------------------------------------------
    def __init__(
        self,
        uri: str,
        name: str,
        parent: Optional[TileDBGroup] = None,
    ):
        """
        See the TileDBObject constructor.
        """
        assert name in ["obs", "var"]
        super().__init__(uri=uri, name=name, parent=parent)
        self.dim_name = name + "_id"

    # ----------------------------------------------------------------
    def shape(self):
        """
        Returns a tuple with the number of rows and number of columns of the `AnnotationDataFrame`.
        The row-count is the number of obs_ids (for `obs`) or the number of var_ids (for `var`).
        The column-count is the number of columns/attributes in the dataframe.
        """
        # TODO: with self._open: see
        # https://github.com/single-cell-data/TileDB-SingleCell/pull/93
        with tiledb.open(self.uri) as A:
            # These TileDB arrays are string-dimensioned sparse arrays so there is no '.shape'.
            # Instead we compute it ourselves.  See also:
            # * https://github.com/single-cell-data/TileDB-SingleCell/issues/10
            # * https://github.com/TileDB-Inc/TileDB-Py/pull/1055
            num_rows = len(A[:][self.dim_name].tolist())
            num_cols = A.schema.nattr
            return (num_rows, num_cols)

    # ----------------------------------------------------------------
    def ids(self) -> List[str]:
        """
        Returns the `obs_ids` in the matrix (for `obs`) or the `var_ids` (for `var`).
        """
        with tiledb.open(self.uri) as A:
            return A[:][self.dim_name].tolist()

    # ----------------------------------------------------------------
    def keys(self) -> List[str]:
        """
        Returns the column names for the `obs` or `var` dataframe.  For obs and varp, `.keys()` is a
        keystroke-saver for the more general array-schema accessor `attr_names`.
        """
        return self.attr_names()

    # ----------------------------------------------------------------
    def dim_select(self, ids):
        """
        Selects a slice out of the dataframe with specified `obs_ids` (for `obs`) or `var_ids` (for `var`).
        If `ids` is `None`, the entire dataframe is returned.
        """
        if ids is None:
            with tiledb.open(self.uri) as A:  # TODO: with self._open
                return A.df[:]
        else:
            with tiledb.open(self.uri) as A:  # TODO: with self._open
                return A.df[ids]

    # ----------------------------------------------------------------
    def df(self, ids=None) -> pd.DataFrame:
        """
        Keystroke-saving alias for `.dim_select()`. If `ids` are provided, they're used
        to subselect; if not, the entire dataframe is returned.
        """
        return self.dim_select(ids)

    # ----------------------------------------------------------------
    # TODO: this is a v1 for prototype/demo timeframe -- needs expanding.
    def attribute_filter(self, query_string, col_names_to_keep):
        """
        Selects from obs/var using a TileDB-Py `QueryCondition` string such as
        'cell_type == "blood"'. Returns None if the slice is empty.
        This is a v1 implementation for the prototype/demo timeframe.
        """
        with tiledb.open(self.uri) as A:
            qc = tiledb.QueryCondition(query_string)
            slice_query = A.query(attr_cond=qc)
            slice_df = slice_query.df[:][col_names_to_keep]
            nobs = len(slice_df)
            if nobs == 0:
                return None
            else:
                return slice_df

    # ----------------------------------------------------------------
    def from_dataframe(self, dataframe: pd.DataFrame, extent: int) -> None:
        """
        Populates the obs/ or var/ subgroup for a SOMA object.

        :param dataframe: anndata.obs, anndata.var, anndata.raw.var.
        :param extent: TileDB extent parameter for the array schema.
        """

        offsets_filters = tiledb.FilterList(
            [tiledb.PositiveDeltaFilter(), tiledb.ZstdFilter(level=-1)]
        )
        dim_filters = tiledb.FilterList([tiledb.ZstdFilter(level=-1)])
        attr_filters = tiledb.FilterList([tiledb.ZstdFilter(level=-1)])

        if self._verbose:
            s = util.get_start_stamp()
            print(f"{self._indent}START  WRITING {self.uri}")

        # Make the row-names column (barcodes for obs, gene names for var) explicitly named.
        # Otherwise it'll be called '__tiledb_rows'.
        #
        # Before:
        #
        #   >>> anndata.obs
        #                  orig.ident nCount_RNA nFeature_RNA ...
        #   ATGCCAGAACGACT 0          70.0       47           ...
        #   CATGGCCTGTGCAT 0          85.0       52           ...
        #   ...            ...        ...        ...          ...
        #   GGAACACTTCAGAC 0          150.0      30           ...
        #   CTTGATTGATCTTC 0          233.0      76           ...
        #
        # After:
        #
        #   >>> anndata.obs.rename_axis('obs_id')
        #                  orig.ident nCount_RNA nFeature_RNA ...
        #   obs_id
        #   ATGCCAGAACGACT 0          70.0       47           ...
        #   CATGGCCTGTGCAT 0          85.0       52           ...
        #   ...            ...        ...        ...          ...
        #   GGAACACTTCAGAC 0          150.0      30           ...
        #   CTTGATTGATCTTC 0          233.0      76           ...
        dataframe = dataframe.rename_axis(self.dim_name)

        mode = "ingest"
        if self.exists():
            mode = "append"
            if self._verbose:
                print(f"{self._indent}Re-using existing array {self.uri}")

        # Context: https://github.com/single-cell-data/TileDB-SingleCell/issues/99.
        # TODO: when UTF-8 attributes are queryable using TileDB-Py's QueryCondition API we can remove this.
        column_types = {}  # XXX None OR {} ?
        if self.name in self._soma_options.col_names_to_store_as_ascii:
            col_names_to_store_as_ascii = (
                self._soma_options.col_names_to_store_as_ascii[self.name]
            )
            for col_name in col_names_to_store_as_ascii:
                column_types[col_name] = np.dtype("S")

        tiledb.from_pandas(
            uri=self.uri,
            dataframe=dataframe,
            name=self.name,
            sparse=True,
            allows_duplicates=False,
            offsets_filters=offsets_filters,
            attr_filters=attr_filters,
            dim_filters=dim_filters,
            capacity=100000,
            tile=extent,
            column_types=column_types,
            ctx=self._ctx,
            mode=mode,
        )

        if self._verbose:
            print(util.format_elapsed(s, f"{self._indent}FINISH WRITING {self.uri}"))

    # ----------------------------------------------------------------
    def to_dataframe(self) -> pd.DataFrame:
        """
        Reads the TileDB obs or var array and returns a type of pandas dataframe
        and dimension values.
        """

        if self._verbose:
            s = util.get_start_stamp()
            print(f"{self._indent}START  read {self.uri}")

        with tiledb.open(self.uri) as A:
            # We could use A.df[:] to set the index_name to 'obs_id' or 'var_id'.
            # However, the resulting dataframe has obs_id/var_id as strings, not
            # bytes, resulting in `KeyError` elsewhere in the code.
            df = pd.DataFrame(A[:])
            df = df.set_index(self.dim_name)

        if self._verbose:
            print(util.format_elapsed(s, f"{self._indent}FINISH read {self.uri}"))

        return df