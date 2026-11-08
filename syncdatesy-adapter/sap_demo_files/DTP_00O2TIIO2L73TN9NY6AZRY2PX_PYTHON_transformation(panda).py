# © 2026 Spectra Solutions. All rights reserved. For internal use only
# DTP : DTP_00O2TIIO2L73TN9NY6AZRY2PX
# Source : RSDS 0FI_GL_4 (source system FADCLNT100) - LANDING.0FI_GL_4
# Target : ADSO /IMO/CMFIGL04 - LANDING./IMO/CMFIGL04_STRM
# DTP graph : RSDS 0FI_GL_4 -> TRFN 0LTUK2CTVE74FDCWJIZTX29JZRV6ROF6 (ABAP) -> ADSO /IMO/CMFIGL04
# Audit report reference : DTP_00O2TIIO2L73TN9NY6AZRY2PX_audit_report.pdf
# Infoarea : /IMO/FIGL_CM
# Script Generated date : 10/02/2026
# ----------------------------------------------------------------------------
import pandas as pd

TARGET_FIELDS = (
    "BUKRS",
    "FISCPER",
    "BELNR",
    "BUZEI",
    "STATUSPS",
    "KTOPL",
    "HKONT",
    "KOART",
    "UMSKZ",
    "BLART",
    "BSCHL",
    "FISCVAR",
    "BLDAT",
    "BUDAT",
    "CPUDT",
    "VALUT",
    "AUGDT",
    "AUGBL",
    "KOKRS",
    "KOSTL",
    "PRCTR",
    "AUFNR",
    "PROJK",
    "WERKS",
    "GSBER",
    "FKBER",
    "PPRCT",
    "PARGB",
    "VBUND",
    "LCURR",
    "DMSOL",
    "DMHAB",
    "DMSHB",
    "WAERS",
    "WRSOL",
    "WRHAB",
    "WRSHB",
    "CURT2",
    "HWAE2",
    "DMSO2",
    "DMHA2",
    "DMSH2",
    "CURT3",
    "HWAE3",
    "DMSO3",
    "DMHA3",
    "DMSH3",
    "XBLNR",
    "XREF3",
    "AWREF",
    "AWGJA",
    "AWBUK",
    "AWKOK",
    "SGTXT",
    "UPDMOD",
    "PS_POSID",
    "ZUONR",
    "BKTXT",
    "GLVOR",
    "PERNR",
    "FISTL",
    "GEBER",
    "KTOSL",
    "BUDGET_PD",
    "UPD_DATE",
    "GN_R3_SSY",
)

DIRECT_FIELDS = (
    "BUKRS",
    "FISCPER",
    "BELNR",
    "BUZEI",
    "STATUSPS",
    "KTOPL",
    "HKONT",
    "KOART",
    "UMSKZ",
    "BLART",
    "BSCHL",
    "FISCVAR",
    "BLDAT",
    "BUDAT",
    "CPUDT",
    "VALUT",
    "AUGDT",
    "AUGBL",
    "KOKRS",
    "KOSTL",
    "PRCTR",
    "AUFNR",
    "PROJK",
    "WERKS",
    "GSBER",
    "FKBER",
    "PPRCT",
    "PARGB",
    "VBUND",
    "LCURR",
    "DMSOL",
    "DMHAB",
    "DMSHB",
    "WAERS",
    "WRSOL",
    "WRHAB",
    "WRSHB",
    "CURT2",
    "HWAE2",
    "DMSO2",
    "DMHA2",
    "DMSH2",
    "CURT3",
    "HWAE3",
    "DMSO3",
    "DMHA3",
    "DMSH3",
    "XBLNR",
    "XREF3",
    "AWREF",
    "AWGJA",
    "AWBUK",
    "AWKOK",
    "SGTXT",
    "UPDMOD",
    "PS_POSID",
    "ZUONR",
    "BKTXT",
    "GLVOR",
    "PERNR",
    "FISTL",
    "GEBER",
    "KTOSL",
    "BUDGET_PD",
)

AMOUNT_FIELDS_13_2 = (
    "DMSOL",
    "DMHAB",
    "DMSHB",
    "WRSOL",
    "WRHAB",
    "WRSHB",
    "DMSO2",
    "DMHA2",
    "DMSH2",
    "DMSO3",
    "DMHA3",
    "DMSH3",
)

BUKRS_FILTER_VALUES = ("IN01", "1710",)
AMOUNT_LIMIT = 10 ** 11
UPD_DATE_INITIAL = "00000000"
GN_R3_SSY_INITIAL = ""


def transform(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    bukrs = df["BUKRS"].astype("string").str.rstrip(" ")
    keep = bukrs.isin(BUKRS_FILTER_VALUES).fillna(False).to_numpy(dtype=bool)
    src = df.loc[keep, DIRECT_FIELDS]

    amounts = src.loc[:, AMOUNT_FIELDS_13_2].apply(pd.to_numeric, errors="coerce")
    overflow = (amounts.abs() >= AMOUNT_LIMIT).any(axis=1).to_numpy(dtype=bool)
    if overflow.any():
        bad = src.loc[overflow, ["BUKRS", "BELNR", "BUZEI", "FISCPER"]].astype(str).head(20)
        raise ValueError(
            "Amount does not fit CURR 13,2 for: "
            + ", ".join("/".join(row) for row in bad.itertuples(index=False))
        )

    df = src.copy()
    df["UPD_DATE"] = UPD_DATE_INITIAL
    df["GN_R3_SSY"] = GN_R3_SSY_INITIAL
    df = df.loc[:, TARGET_FIELDS].reset_index(drop=True)
    return df
