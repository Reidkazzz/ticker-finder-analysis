"""Company financials from SEC XBRL "frames": one request returns a single line item for every filer."""
import datetime as dt
import json
import math
import os
import statistics
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .net import NotFound, Throttled, sec_json

# Capex tags are combined by _capex(). Depreciation only tells whether a company without a readable capex
# tag must still have capex.
CAPEX = [
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsToExploreAndDevelopOilAndGasProperties",
    "PaymentsToAcquireOilAndGasPropertyAndEquipment",
    "PaymentsToAcquireOilAndGasProperty",
    # The property purchases of companies that tag no other capex line (Eli Lilly's 2025: $7.84B, its only capex tag).
    # On September 2026 data 88 companies tagged it for 2025, 67 of them with no other capex tag, so their free cash flow
    # could not be read.
    "PaymentsToAcquireOtherPropertyPlantAndEquipment",
]
DEPRECIATION = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "Depreciation"]
PRETAX = ["IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
          "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"]
# Write-downs other than goodwill, combined by _impairments(). Companies tag the same charge in different ways
# (one total, or pieces that may overlap), so the largest reading counts rather than the sum of all of them.
IMPAIRMENT = [
    "AssetImpairmentCharges",  # often the total, goodwill included
    "GoodwillAndIntangibleAssetImpairment",
    "ImpairmentOfIntangibleAssetsExcludingGoodwill",
    "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill",
    "ImpairmentOfIntangibleAssetsFinitelived",
    "ImpairmentOfLongLivedAssetsHeldForUse",
    "ImpairmentOfLongLivedAssetsToBeDisposedOf",
    "ImpairmentOfRealEstate",
    "TangibleAssetImpairmentCharges",
    "OtherAssetImpairmentCharges",
    "OperatingLeaseImpairmentLoss",
    "ImpairmentOfOilAndGasProperties",
]
# Write-downs that can't be reversed (see _impairments).
NO_REVERSAL = {"GoodwillImpairmentLoss", "GoodwillAndIntangibleAssetImpairment", "ImpairmentOfIntangibleAssetsExcludingGoodwill",
               "ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill", "ImpairmentOfIntangibleAssetsFinitelived"}
# Gains (positive) and losses (negative) on selling a business or assets, combined by _sale(). The first four are
# businesses and stakes in them, the rest assets.
SALE = [
    "GainLossOnSaleOfBusiness",
    "DisposalGroupNotDiscontinuedOperationGainLossOnDisposal",
    "DeconsolidationGainOrLossAmount",
    "EquityMethodInvestmentRealizedGainLossOnDisposal",
    "GainLossOnDispositionOfAssets",
    "GainLossOnDispositionOfAssets1",
    "GainLossOnSaleOfPropertyPlantEquipment",
    "GainLossOnSaleOfOtherAssets",
    "GainsLossesOnSalesOfAssets",
    "GainLossOnDispositionOfIntangibleAssets",
]
# Gains (positive) and losses on selling real estate, which REITs report on their own lines. Only a REIT's are read,
# since funds from operations, the REIT earnings measure, leaves them out, while selling property it developed is part
# of the business for a developer or a real estate services firm (CBRE's development sales). The third is the tag banks
# use for property they took over, which some REITs use for their own (CBL & Associates' 2025: $74.2M).
PROPERTY_SALE = ["GainsLossesOnSalesOfInvestmentRealEstate", "GainLossOnSaleOfProperties",
                 "GainsLossesOnSalesOfOtherRealEstate"]
# Other gains funds from operations leaves out, each a separate deal and so added together, read only for REITs: the
# profit booked when a building is leased out on terms that count as selling it (Vornado's 2025: $803.2M), and gains on
# taking control of a venture (Douglas Emmett's 2025: $47.2M; American Healthcare REIT's: $14.6M).
REIT_GAIN = ["SalesTypeLeaseSellingProfitLoss", "VariableInterestEntityInitialConsolidationGainOrLoss",
             "BusinessCombinationStepAcquisitionEquityInterestInAcquireeRemeasurementGain"]
# Depreciation and amortization a REIT adds back to its net income to reach funds from operations (_ffo): the first of
# these it tags. Against the FFO 116 REITs reported for 2025 (read from their 10-Ks), FFO built on the cash flow
# statement's figure (first) missed by a median 4.8%, on the income statement's by 5.1%, and on the property
# depreciation of their Schedule III, which leaves out lease intangibles, by 18%.
REIT_DEPRECIATION = ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                     "DepreciationAmortizationAndAccretionNet", "Depreciation",
                     "CostOfGoodsAndServicesSoldDepreciationAndAmortization"]  # hotel REITs (DiamondRock, Pebblebrook)
# Income tax credits from the tax rate reconciliation, which a company earns from what it does every year (research,
# restaurant tips, clean energy), so they are part of its normal tax. The first is the total. Foreign tax credits are
# left out, since they only offset foreign tax. Reported with either sign, so they are read as amounts.
TAX_CREDITS = [
    "IncomeTaxReconciliationTaxCredits",
    "IncomeTaxReconciliationTaxCreditsResearch",
    "IncomeTaxReconciliationTaxCreditsOther",
    "IncomeTaxReconciliationTaxCreditsInvestment",
]
# Gains and losses on investment securities, sold or revalued, such as a bank selling bonds at a loss to reinvest at
# higher rates (Tompkins Financial's 2025: $78.7M). Combined like the sale tags.
SECURITIES = [
    "DebtSecuritiesAvailableForSaleRealizedGainLoss",
    "MarketableSecuritiesRealizedGainLossExcludingOtherThanTemporaryImpairments",
    "DebtSecuritiesRealizedGainLoss",
    "DebtAndEquitySecuritiesGainLoss",
]
# A business development company's total investment income, its revenue (DURATION's revenue tags, _revenue_tags).
INVESTMENT_INCOME = "GrossInvestmentIncomeOperating"
# Marks a fiscal year whose revenue may not be read from it, as the company's newer years tag revenue otherwise
# (load_fundamentals, _revenue_tags).
NOT_BDC = "investment_income_not_revenue"
# Each metric lists the XBRL tags companies use for it, in order of preference.
DURATION = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "RevenuesNetOfInterestExpense",
        # Utilities that tag their total sales only this way (Xcel Energy's 2025: $14.67B, DTE Energy's $15.81B, MGE
        # Energy's $744M, each as their 10-K shows), which left them without a sales figure.
        "RegulatedAndUnregulatedOperatingRevenue",
        # A business development company's total investment income: the interest, dividends and fees it earns on the
        # loans and stakes it holds, the revenue these funds report (Ares Capital's 2025: $3.05B, as its 10-K shows).
        # BDCs tag no other revenue line, so without it they had no figures at all. Insurers tag it too, for what their
        # portfolio earns beside the premiums that are most of their revenue, so it never counts for a year a company
        # tags premiums earned, or whose premiums frame failed (_revenue_tags). It stays last, so a company that tags any
        # other revenue line keeps that one.
        INVESTMENT_INCOME,
    ],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"],
    "operating_income": ["OperatingIncomeLoss"],
    "ocf": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": CAPEX + DEPRECIATION,
    # For the cash-flow model (report._cash_flow_model), which values the business before its debt and then subtracts
    # the debt. Cash paid for interest, which operating cash flow is counted after, so the model adds it back after tax
    # (1,924 of the 2,168 companies with debt and free cash flow figures tagged it for their latest year on September
    # 2026 data). Stock-based pay, which operating cash flow adds back as noncash, though it costs shareholders as the
    # new shares dilute them, so the model takes it off (the first tag is the cash flow statement's add-back, the second
    # the expense, equal to it at the median of the 2,477 companies that tagged both for 2025). And capital spending
    # that some companies report on its own line rather than with the property purchases free cash flow counts (CAPEX):
    # software built or bought for their own use (248 companies, a median 13% of free cash flow for the 159 of them
    # whose free cash flow was positive; only four tagged both software lines for 2025, so the larger counts), and
    # construction (21 companies for 2025, among them American Electric Power's $8.45B, its main capital spending,
    # beside $3.45B tagged as other productive assets, and Matson's $244M of ship construction beside $393M of other
    # capital spending).
    "interest_paid": ["InterestPaidNet", "InterestPaid"],
    # Interest expense, which stands in for interest paid where a company tags none (Mastercard's 2025: $680M on its
    # debt, which it tagged as interest paid for 2024, $571M, the same as its interest expense on debt that year;
    # Qualcomm's $664M). Read with InterestExpense, which the bank lines already fetch (bank_interest_expense).
    "interest_expense": ["InterestExpenseDebt", "InterestExpenseNonoperating"],
    "sbc": ["ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"],
    "software_capex": ["PaymentsToDevelopSoftware", "PaymentsForSoftware"],
    "construction_capex": ["PaymentsForConstructionInProcess"],
    # The year's change in working capital, a use of cash where positive, whose outliers mark a one-off payment inside
    # operating cash flow (_one_off_cash): Coca-Cola's $6.23B in 2024, holding its $6.0B deposit with the IRS in its tax
    # dispute, and $7.21B in 2025, holding $6.1B of its final payment for fairlife, both as its 10-K describes; against
    # $0.9B or less in each of 2021 to 2023. Payments on an acquisition's earn-out that count as operating cash
    # (PaymentForContingentConsiderationLiabilityOperatingActivities) are not added back: for most of the companies
    # tagging them they recur, royalty-like, year after year (AbbVie's $0.87B, $2.0B and $2.87B in 2023 to 2025), and
    # the rest still owed would have to be subtracted as a debt too.
    "working_capital": ["IncreaseDecreaseInOperatingCapital"],
    # The pieces between operating income and net income, read by _adjust(). The first pretax tag already
    # includes income from equity-method investments, the second leaves it out.
    "pretax": PRETAX,
    "income_tax": ["IncomeTaxExpenseBenefit"],
    "discontinued": ["IncomeLossFromDiscontinuedOperationsNetOfTax", "IncomeLossFromDiscontinuedOperationsNetOfTaxAttributableToReportingEntity"],
    "equity_method": ["IncomeLossFromEquityMethodInvestments"],
    "minority": ["NetIncomeLossAttributableToNoncontrollingInterest"],
    # One-time items, read by _adjust(). The goodwill write-down is kept apart because it is usually not tax
    # deductible. The last four say how much tax a goodwill write-down saved and what drives a tax bill.
    "gw_impairment": ["GoodwillImpairmentLoss"],
    "impairment": IMPAIRMENT,
    "sale": SALE,
    "property_sale": PROPERTY_SALE,
    "reit_gain": REIT_GAIN,
    "securities": SECURITIES,
    "debt_extinguishment": ["GainsLossesOnExtinguishmentOfDebt"],
    "gw_impairment_net": ["GoodwillImpairmentLossNetOfTax"],
    "impairment_nondeductible": ["IncomeTaxReconciliationNondeductibleExpenseImpairmentLosses"],
    "valuation_allowance": ["IncomeTaxReconciliationChangeInDeferredTaxAssetsValuationAllowance"],
    "tax_credits": TAX_CREDITS,
    # Lines of the tax rate reconciliation besides the valuation allowance that are often one-time, read by
    # _tax_one_time(): deferred taxes remeasured after a change in tax law (Hewlett Packard Enterprise's 2025: $327M),
    # settlements with tax authorities, tax on selling or moving a business or assets at other than the usual rate
    # (Stryker's 2025: $405M), and changes in reserves for uncertain tax positions. The first settlement tag is the
    # total and the others its pieces; the two disposal tags are separate pieces.
    "tax_law": ["IncomeTaxReconciliationChangeInEnactedTaxRate"],
    "tax_settlement": ["IncomeTaxReconciliationTaxSettlements", "IncomeTaxReconciliationTaxSettlementsDomestic",
                       "IncomeTaxReconciliationTaxSettlementsForeign"],
    "tax_disposal": ["IncomeTaxReconciliationDispositionOfBusiness", "IncomeTaxReconciliationDispositionOfAssets"],
    "tax_contingency": ["IncomeTaxReconciliationTaxContingencies"],
    # For REITs (_ffo): the depreciation tags the capex list lacks, preferred dividends (for a company that doesn't tag
    # the net income left to common shareholders), common dividends paid, which a REIT must keep paying even when
    # depreciation leaves it a loss (report.is_reit), and rent, which some REITs tag as their only sales figure (derive).
    "reit_depreciation": REIT_DEPRECIATION[2:3] + REIT_DEPRECIATION[4:],
    "preferred": ["PreferredStockDividendsIncomeStatementImpact"],
    "dividends": ["PaymentsOfDividendsCommonStock", "PaymentsOfDividends", "DividendsCommonStockCash"],
    "lease_income": ["OperatingLeaseLeaseIncome"],
    # For banks (_bank_year), whose revenue is net interest income (interest earned less interest paid, before the
    # provision for loan losses) plus noninterest income (fees, service charges, trading). Of the 294 banks with 2025
    # figures, 289 tag InterestIncomeExpenseNet and 288 NoninterestIncome; the rest are read from total interest income
    # less interest expense, net interest income after the provision plus the provision, or noninterest income implied
    # by pretax income (net interest income after the provision, plus noninterest income, less noninterest expense,
    # which held within 1% in 795 of 806 bank-years that tag all four). Their "Revenues" tag can't stand in: of the 43
    # banks that tag one for 2025, 25 tagged their revenue, 8 total interest income plus fees (Banc of California's
    # $1.82B, against $1.12B of revenue), 4 their fees alone (Zions' $662M, against $3.39B) and 6 something else.
    "bank_nii": ["InterestIncomeExpenseNet"],
    # The same under a newer tag, which a few banks use instead (Arrow Financial's $133.2M for 2025), kept apart from
    # bank_nii so that its frame failing, which leaves it blank, never stops a run (CRITICAL).
    "bank_nii_alt": ["InterestRevenueExpenseNet"],
    "bank_interest_income": ["InterestAndDividendIncomeOperating", "InterestIncomeOperating"],
    "bank_interest_expense": ["InterestExpenseOperating", "InterestExpense"],
    "bank_nii_after_provision": ["InterestIncomeExpenseAfterProvisionForLoanLoss"],
    "bank_provision": ["ProvisionForLoanLeaseAndOtherLosses", "ProvisionForLoanLossesExpensed",
                       "ProvisionForLoanAndLeaseLosses"],
    "bank_noninterest_income": ["NoninterestIncome"],
    "bank_noninterest_expense": ["NoninterestExpense"],
    # Gains and losses on investment securities under the broader tags 23 banks used in 2022 to 2026 in years they tagged
    # none under SECURITIES (Truist's 2024 loss of $6.65B on selling securities to reinvest at higher rates; Banc of
    # California's 2023 loss of $442M), read only for a bank, where they are part of its noninterest income. Other
    # companies' investment gains under these tags are left as they were.
    "bank_securities": ["GainLossOnInvestments", "DebtSecuritiesAvailableForSaleGainLoss"],
    # Gains and losses on stakes in other companies, revalued or sold, which any other company that tags none under
    # SECURITIES reads under this or GainLossOnInvestments (derive): Nvidia's $8.92B for its year to January 2026, AT&T's
    # $5.89B for 2025 and Walmart's $2.02B for its year to January 2026, against losses or much smaller gains before.
    "equity_securities": ["EquitySecuritiesFvNiGainLoss"],
    # Goodwill from the year's acquisitions, which tells a bank's provision for loan losses swollen by the allowance it
    # had to set up at once for the loans it bought (report._provision_spike) from one swollen by its own lending.
    "bank_goodwill_acquired": ["GoodwillAcquiredDuringPeriod"],
    # Dividends on preferred stock, which say a bank has some (_bank_fields). Unlike the gap between net income and the
    # net income left to common shareholders, they leave out the share of earnings that restricted shares get
    # (BankUnited's $5.7M in 2025, with no preferred stock), and some banks tag them only here (Bank of Hawaii's $21.1M).
    "bank_preferred_dividends": ["DividendsPreferredStock", "DividendsPreferredStockCash"],
    # A business development company's net investment income: its total investment income less its costs (interest on
    # its borrowing, management and incentive fees), before gains and losses on its loans and stakes. It is the earnings
    # these funds are valued on and pay their dividends from, as their 10-Ks show it (Ares Capital's 2025: $1.46B,
    # against $1.30B of net income after the year's losses on its investments; New Mountain Finance's $136.4M against
    # $16.5M), so report.py values a BDC on it rather than on net income, which swings with a year's marks.
    "bdc_nii": ["NetInvestmentIncome"],
}
TAX_LINES =("tax_law", "tax_settlement", "tax_disposal", "tax_contingency")
TAX_TOTALS = {"tax_settlement"}  # lines whose first tag is the total of the others (see _items)
# The same reconciliation lines as shares of pretax income (unit "pure"), which many companies tag instead of dollars
# (EverQuote tagged its valuation allowance only as percentages until 2025), and the effective tax rate the company
# reports, which confirms that its pretax income and tax were read as it reports them.
RATIOS = {
    "etr": ["EffectiveIncomeTaxRateContinuingOperations"],
    **{k + "_pct": ["EffectiveIncomeTaxRateReconciliation" + t[len("IncomeTaxReconciliation"):] for t in DURATION[k]]
       for k in ("valuation_allowance", "tax_credits", *TAX_LINES)},
}
# Line items that only come from a company's financial statements, so the filings that supplied them are annual
# reports (or their amendments and recasts), whose one-time item and revenue figures _check_items() trusts without a
# lookup. Net income and revenue are left out: proxies tag them too (Quinstreet's 2026 proxy tagged a profit measure
# for each of 2022 to 2026 as Revenues, $112.5M for 2026, against $1.29B of sales in its 10-K).
STATEMENT_TAGS = {t for k in ("operating_income", "ocf", "pretax", "income_tax") for t in DURATION[k]}
# Revenue read from a company's own income statement (statements.py), for a listed company whose results the SEC's data
# gives without revenue under any tag above (APA Corporation's "Total revenues", tagged only by product). Its figures
# join the company's fiscal years, quarters and own-filing rows ("concept") under this name, after the frames are read,
# and it is never fetched as a frame or looked up as a tag. Last, so a tagged figure wins.
STATEMENT_REVENUE = "IncomeStatementRevenue"
REVENUE_ORDER = DURATION["revenue"] + [STATEMENT_REVENUE]
REVENUE_TAGS = set(REVENUE_ORDER)
# Without these for the two newest years, every company's latest figures would silently change. A bank's revenue is
# its net interest income plus its noninterest income.
CRITICAL = {"revenue", "net_income", "ocf", "capex", "pretax", "income_tax", "bank_nii", "bank_noninterest_income"}
# One-time item frames. If any of them failed for any year, _items() leaves all of them out for every year: a year
# that merely read as zero would make an item look one-time in the others, and reading only some kinds would take
# out gains without adding back charges or the reverse.
ITEMS = {"gw_impairment", "impairment", "sale", "property_sale", "securities", "debt_extinguishment",
         "gw_impairment_net", "impairment_nondeductible", "valuation_allowance", "tax_credits", "equity_securities"}
# Checked like them (_check_items), though a failed frame of the REIT gains only leaves those out (REIT_METRICS).
ITEM_TAGS = {t for k in ITEMS for t in DURATION[k]} | set(REIT_GAIN) | {"GainLossOnInvestments"}
# Frames that show which years' tax rates are a company's normal one (_clean_rate). A frame that fails for a year is
# taken from the last run that read it (TAX_CACHE), since a past year's filings rarely change. Without that, the lines
# are unknown for that year only, which then shows no company's normal rate, and a failed effective rate frame only
# skips the check it supports.
TAX_EVIDENCE = {*TAX_LINES, *RATIOS}
# Frames only REITs' funds from operations need (_annual). One that fails leaves that year's figure blank; for the REIT
# gains, which only a few REITs tag, that leaves them in FFO for the run rather than every company's one-time items out.
REIT_METRICS = {"reit_depreciation", "preferred", "dividends", "lease_income", "reit_gain"}
REIT_INSTANT = {"liabilities", "reit_debt"}  # likewise for their balance sheets (_reit_debt)
# Likewise for the debt check (_debt_check): noncurrent operating lease liabilities, which are not debt but make up
# most of the noncurrent liabilities of many store and restaurant chains.
DEBT_CHECK_INSTANT = {"lease_nc"}
# Frames only banks need (_bank_year). The two in CRITICAL stop the run for the newest years; any other that fails leaves
# the figures built on it blank for that year.
BANK_METRICS = {k for k in DURATION if k.startswith("bank_")}
# Likewise for business development companies' net investment income: a failed frame leaves that year's figure blank.
BDC_METRICS = {"bdc_nii"}
# Frames only the cash-flow model needs. One that fails is taken from the last run that read it (CASH_FLOW_CACHE), as
# the tax frames are, except for companies that filed for that year since (_kept_frames); without that, the figure is
# unknown for that year, which then leaves the year out of the model.
CASH_FLOW_METRICS = {"interest_paid", "interest_expense", "sbc", "software_capex", "construction_capex",
                     "working_capital"}
# Balance sheet lines for a bank's tangible book value (_bank_parts). One that fails leaves it unknown for that quarter,
# rather than reading as none.
BANK_INSTANT = {"goodwill", "intangibles", "preferred_stock", "servicing"}
# The largest share of net income or sales that preferred dividends and the like can take from common shareholders
# before a gap between net income and the net income left to common shareholders is taken for a misread (_common_gap).
COMMON_GAP_MAX = 0.25
# The least rent, as a share of total assets, that makes a REIT's rent its sales figure when it tags no other (derive).
# In 2025, landlords' rent came to 8% to 15% of their assets, mortgage REITs' to under 2.5%.
LEASE_MIN = 0.04
TAX_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "tax_frames.json")
CASH_FLOW_CACHE = os.path.join(os.path.dirname(TAX_CACHE), "cash_flow_frames.json")

# Gross profit (_gross_profit): the company's own gross profit line, or else its revenue less its cost of revenue, the
# larger of the two cost tags where it tags both (one can be a part of the other). Read for each fiscal year and quarter.
# Tags that leave depreciation out of the cost (CostOfGoodsAndServiceExcludingDepreciationDepletionAndAmortization, 234
# companies for 2025) are not read, since a gross profit without the wear on the equipment that makes the goods
# overstates it. Frames that fail leave that year's gross profit unknown for every company, rather than a company that
# tags its own line read from its cost tags instead.
GROSS = {"gross_profit": ["GrossProfit"], "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold"]}
# A cost of revenue line counts only when it is at least this share of everything the company spent to earn its revenue
# (revenue less operating income): most of the costs of a retailer, distributor or manufacturer that shows no gross
# profit line (Walmart's 2025 cost of sales came to 78% of its costs, Amazon's to 56%, Costco's to 91%). A smaller line
# is often only a part of the direct costs: a restaurant's food (Bloomin' Brands' came to 30% of its costs, which would
# give a 70% gross margin), a utility's fuel (CenterPoint's, 0.1%), a health insurer's pharmacy products (UnitedHealth's,
# 12%, giving 89%) or an oil producer's (Matador's, 8%). On September 2026 data, of the 1,750 companies outside finance
# that tag their own gross profit line and operating income, 58% had a cost of revenue of at least this share, and of
# the 442 that tag only a cost line, 46%.
COST_SHARE_MIN = 0.5
# A smaller cost line still counts where it and the company's overhead (OVERHEAD: research and development, and selling,
# general and administrative costs, or selling and marketing plus general and administrative) come to EXPLAINED of its
# costs: nothing large is left between them that could be more of the direct cost, as with a drug maker or a software
# company, whose direct costs are small (Meta's cost of revenue came to 31% of its 2025 costs and, with its overhead,
# 100%; Gilead's to 32% and 92%). A partial line leaves much of the costs unexplained: a restaurant's labor and rent
# (Bloomin' Brands' 36%, Yum Brands' 66%), a utility's operations and depreciation (WEC Energy's 43%), an oil producer's
# field costs (EQT's 35%), a health insurer's medical costs (UnitedHealth's 26%). Of the 240 companies whose cost line
# fell short of COST_SHARE_MIN on 2025 data, 151 passed this test (139 of 235 on 2024 data); of the 1,750 that tag their
# own gross profit line, 84% would have passed it had they tagged only the cost (89% passing either test). An overhead
# frame that fails only leaves this test out for that year, which can hide a gross profit but never show a wrong one.
OVERHEAD = {"research": ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost"],
            "sga": ["SellingGeneralAndAdministrativeExpense"],
            "selling": ["SellingAndMarketingExpense"], "admin": ["GeneralAndAdministrativeExpense"]}
EXPLAINED = (0.85, 1.10)
# Premiums earned and the benefits and claims paid on them, which make a company partly an insurer (CVS Health's 2025
# premiums came to 34% of its revenue, Cigna's benefit costs to 97%, Molina's premiums to 95%). Gross profit means
# nothing for an insurer, as for other financial companies, so a company whose premiums or benefits come to INSURER_SHARE
# of its revenue has none: CVS Health's cost of products sold leaves out the medical costs of its Aetna insurance, which
# would give it a 45% gross margin. Frames that fail leave that year's gross profit unknown for every company.
INSURANCE = {"premiums": ["PremiumsEarnedNet"],
             "benefits": ["PolicyholderBenefitsAndClaimsIncurredNet", "BenefitsLossesAndExpenses"]}
INSURER_SHARE = 0.1
# A cost of revenue line under the generic tags can still leave out the depreciation of the equipment that earns the
# revenue, shown on a line of its own: Lumen's "cost of services and products (exclusive of depreciation and
# amortization)" ($6.64B for 2025, beside $2.75B of depreciation and amortization) would give a 46.5% gross margin
# instead of 24%, Royal Caribbean's cruise operating expenses 49% instead of 40%, Waste Management's 40% instead of 29%.
# Such a line shows as costs that the cost line and the overhead (OVERHEAD) leave unexplained coming to about the
# depreciation: the depreciation and amortization the cash flow statement adds back (DEPRECIATION, the largest reading)
# less the amortization of acquired intangibles (AMORTIZATION), which companies usually show on a line of their own
# whatever their cost of revenue includes. A cost line whose unexplained costs, less that amortization, come to
# DEPRECIATION_LEFT_OUT of the depreciation, where the depreciation is at least DEPRECIATION_MIN of revenue, counts as
# partial: on September 2026 data, 31 of the 370 companies whose 2025 gross profit was read from a cost line, among them
# Southern Copper, Verisk, Clean Harbors, Norwegian Cruise Line, Casella Waste and Liberty Energy. Smaller depreciation
# overstates a margin by fewer points, and a company's own gross profit line stands. An amortization frame that fails
# counts as no amortization, which can hide a gross profit but never show a wrong one.
AMORTIZATION = {"amortization": ["AmortizationOfIntangibleAssets"]}
DEPRECIATION_MIN = 0.06
DEPRECIATION_LEFT_OUT = (0.75, 1.4)
# A gross profit line and a cost of revenue line that add up to another revenue tag's figure, more than FOOTING_GAP
# from the revenue figure used, show that the revenue is on another footing than the gross profit (_off_footing):
# NetApp's "Revenues" for its quarter to July 31, 2026 ($1.82B) is its hybrid cloud business alone, while its gross
# profit and cost of revenue ($1.42B and $0.61B) add up to its revenue from customer contracts ($2.03B); Molson Coors'
# revenue for its quarter to June 30, 2026 ($3.60B) is before the excise taxes its gross profit and cost of sales leave
# out ($1.06B and $2.03B, adding up to $3.10B). A margin on such a revenue figure would be off, so none is shown. Where
# the two lines add up to no revenue figure at all, the cost line is only part of the direct cost and the company's own
# gross profit line stands.
FOOTING_GAP = 0.03

# Quarterly figures (derive's quarters): revenue under the same tags as the annual figure, a bank's net interest and
# noninterest income (its revenue, as _bank_year counts it), and the gross profit tags. One frame per tag and calendar
# quarter holds each company's three-month figure that best fits that quarter, from its latest filing, so companies
# whose fiscal quarters don't follow the calendar are there too (Walmart's May to July quarter is in the second
# quarter's frame). QUARTER_FRAMES quarters are read, the open one included (Adobe's quarter to August 28 is already in
# the third quarter's frame by late September): twelve quarters to show, four more for each one's growth over the year
# before, and three more for the oldest of those when it is a fourth quarter worked out from its year (Apple's to
# September 2022 needs its quarters from October 2021). A bank's other ways of reading its revenue (_bank_year) are
# not read quarterly: on September 2026 data 285 of the 294 banks tagged both parts directly for the first quarter of
# 2026, and the rest go without quarters.
QUARTERLY = {"revenue": DURATION["revenue"], **GROSS, "bank_nii": DURATION["bank_nii"],
             "bank_nii_alt": DURATION["bank_nii_alt"], "bank_noninterest_income": DURATION["bank_noninterest_income"]}
QUARTER_FRAMES = 20
QUARTERS_SHOWN = 12
# A frame that fails is taken from the last run's copy (only each company's value, dates and filing), as the tax frames
# are, since a past quarter's figures change only when a later filing repeats or restates them; with no copy, that
# quarter's figures under that metric are unknown for every company.
QUARTER_CACHE = os.path.join(os.path.dirname(TAX_CACHE), "quarter_frames.json")
# Each frame keeps only the latest filed figure for a period, so a quarter and the year or the year-ago quarter it is
# combined with can come from filings on different footings: a later 10-Q or 10-K restates the figures it repeats after
# a business is sold or an accounting change (Kontoor Brands' 2026 10-Qs gave its first two quarters of 2025 without its
# Lee brand, $423M and $493M against $623M and $658M as first filed, beside its third quarter and its year as first
# filed, which would give a fourth quarter of $1.38B instead of the $1.02B it reported; Bank of America's 2026 10-K
# restated its 2023 and 2024 revenue, beside quarters as first filed). Nothing in the frames tells such figures apart, so
# each listed company's own 10-Q and 10-K figures under the revenue tags its quarters are read from (a bank's net
# interest income and noninterest income) are looked up (companyconcept, one request per tag) and kept in QUARTER_FACTS:
# {"cik|tag": {"checked": date, "rows": [[start, end, value, accession number, date filed], ...] for each three-month
# and one-year figure of each filing, "unmatched": {accession number: date first unmatched}}}. Each filing that gives a
# figure other than the last filing before it gave for the same period (by more than RESTATED_GAP) starts a new
# footing (_footings), and figures are only combined (a fourth quarter worked out from its year, growth on the year
# before, the last twelve months) where one footing's filings give them all as shown, or they come from one filing. A
# tag is looked up again when the frames show a filing its rows lack (a new 10-Q or 10-K), and while one stays
# unmatched (the frames also carry figures from other filings, such as an 8-K giving a business bought, which never
# match), every QUARTER_RECHECK_DAYS for QUARTER_UNMATCHED_DAYS after it was first seen. New filings are looked up
# first, then companies never looked up, the most valuable first, within QUARTER_LOOKUP_MINUTES a run
# (QUARTER_SEED_MINUTES on a run with more than QUARTER_SEED_TAGS never looked up, as the first is, with about 6,900
# tags); the rest wait for the next run, and until then their figures from different filings are not combined. The
# same rows fill the periods the frames lack (_quarter_lookups): a frame holds one figure per company, so where two of a
# company's quarters best fit the same calendar quarter one of them is left out (Costco's 12-week second quarter, late
# November to mid-February, loses to its third in the first quarter's frame), and an annual frame sometimes leaves out
# a fiscal year (Lam Research's to June 30, 2024). A company's gross profit tag (or its cost of revenue tag,
# _gross_profit's basis) is looked up the same way, since a later filing can restate gross profit alone, moving costs
# into or out of the cost of sales while revenue stands: a fourth quarter's gross profit, and the last twelve months',
# are only worked out where their gross profit figures are on one footing.
QUARTER_FACTS = os.path.join(os.path.dirname(TAX_CACHE), "quarter_facts.json")
QUARTER_UNMATCHED_DAYS = 30
QUARTER_RECHECK_DAYS = 7
QUARTER_LOOKUP_MINUTES = 10
QUARTER_SEED_MINUTES = 20
QUARTER_SEED_TAGS = 1000
RESTATED_GAP = 0.002

# Debt, read by _debt(). Instrument tags (convertibles, notes, credit lines) often restate pieces of the
# broad totals in footnotes, so they only count when they add up to more than the broad tags do.
DEBT_FAMILIES = [  # (whole amount, noncurrent part, current part)
    (["ConvertibleNotesPayable"], ["ConvertibleDebtNoncurrent", "ConvertibleLongTermNotesPayable"],
     ["ConvertibleNotesPayableCurrent", "ConvertibleDebtCurrent"]),
    (["NotesPayable"], ["LongTermNotesPayable"], ["NotesPayableCurrent"]),
    (["LineOfCredit"], ["LongTermLineOfCredit"], ["LinesOfCreditCurrent"]),
    (["SeniorNotes"], [], []),
    (["SecuredDebt"], [], []),
    (["LoansPayable"], [], []),
    ([], ["OtherLongTermDebtNoncurrent"], []),
]
DEBT_TOTALS = ["DebtLongtermAndShorttermCombinedAmount", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities"]
DEBT = [
    "LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesAndLoans", "LongTermDebt",
    "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent",
    "DebtCurrent", "ShortTermBorrowings", "CommercialPaper",
    *DEBT_TOTALS, *(t for family in DEBT_FAMILIES for part in family for t in part),
]
INSTANT = {
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"],
    "st_investments": ["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"],
    "current_assets": ["AssetsCurrent"],
    "current_liabilities": ["LiabilitiesCurrent"],
    "debt": DEBT,
    # Only for matching similar companies (sales per dollar of assets), so a failed request leaves it blank rather than
    # costing the quarter's whole balance sheet.
    "total_assets": ["Assets"],
    # Only for REITs (_reit_debt): total liabilities, which show whether their debt was read at all, and the debt tags
    # many REITs use instead of those in DEBT (Highwoods Properties' $3.7B of mortgages and notes, Kilroy Realty's $4.0B
    # of unsecured debt). A failed request leaves them blank.
    "liabilities": ["Liabilities"],
    "reit_debt": ["DebtInstrumentCarryingAmount", "NotesAndLoansPayable", "NotesPayableToBank", "UnsecuredDebt",
                  "DebtAndCapitalLeaseObligations"],
    # Only for the debt check (_debt_check). A failed request leaves its liabilities test unmade for that quarter.
    "lease_nc": ["OperatingLeaseLiabilityNoncurrent"],
    # Marketable securities held for over a year, which count with cash (derive): Apple's $84.1B at June 2026, Regeneron's
    # $9.8B. Other long-term investments are left out, since at an insurer or a health insurer they back what it owes
    # policyholders (UnitedHealth's $57.7B), and a stake in another company is not cash. A failed request leaves it out.
    "lt_securities": ["MarketableSecuritiesNoncurrent"],
    # The part of consolidated subsidiaries that minority holders own, in equity and in temporary equity where they can
    # make the company buy it (AT&T's June 2026: $16.0B and $2.0B; CF Industries' $3.2B, CHS's stake in its nitrogen
    # business). The sales, cash flow and debt the valuation reads are the whole group's, so this claim comes off with
    # the debt (report.other_claims). Added together, as they are separate lines. A failed request leaves it out.
    "minority_interest": ["MinorityInterest", "RedeemableNoncontrollingInterestEquityCarryingAmount"],
    # Only for banks' tangible book value (_bank_parts): goodwill, other intangible assets (the last tag is both
    # together) and preferred stock, which belongs to preferred shareholders rather than common ones.
    "goodwill": ["Goodwill"],
    "intangibles": ["IntangibleAssetsNetExcludingGoodwill", "FiniteLivedIntangibleAssetsNet",
                    "IndefiniteLivedIntangibleAssetsExcludingGoodwill", "OtherIntangibleAssetsNet",
                    "IntangibleAssetsNetIncludingGoodwill"],
    # The last is its liquidation preference, which counts where the others give only a nominal par value (_bank_parts).
    "preferred_stock": ["PreferredStockValue", "PreferredStockValueOutstanding",
                        "PreferredStockIncludingAdditionalPaidInCapitalNetOfDiscount",
                        "PreferredStockIncludingAdditionalPaidInCapital", "PreferredStockLiquidationPreferenceValue"],
    # Mortgage servicing rights, which banks leave in their tangible book value although some tag them inside their
    # other intangible assets (_bank_parts).
    "servicing": ["ServicingAsset", "ServicingAssetAtFairValueAmount", "ServicingAssetAtAmortizedValue"],
}
# The common shares outstanding on a bank's balance sheet date (_bank_parts), which a cover page's count, dated the
# filing before, can predate by a merger (FirstSun Capital's June 2026 balance sheet, with First Foundation in it,
# beside the 27.9M shares on its first-quarter report's cover). Its frame failing leaves the cover's count to use.
BANK_SHARES = "CommonStockSharesOutstanding"
OPTIONAL_INSTANT = {"shares_out", "total_assets", "liabilities", "reit_debt", "bank_shares", *BANK_INSTANT,
                    *DEBT_CHECK_INSTANT, "lt_securities", "minority_interest"}
# A company's balance sheet date is the newest quarter in which it reported one of these.
ANCHORS = INSTANT["equity"] + INSTANT["current_assets"]

RETRY_PAUSE = 60  # seconds to wait before retrying frames that failed


def _years(today):
    # A frame files each fiscal year under the calendar year it mostly covers (a July-June year counts for the
    # year it ends), so from July the current calendar year already holds filed annual reports. derive() uses
    # each company's newest filed year, and the fifth year keeps four full years for companies still to file.
    last = today.year if today.month >= 7 else today.year - 1
    return list(range(last - 4, last + 1))


def _quarters(today, n=5):
    q = (today.month - 1) // 3  # the current quarter is still open
    y = today.year
    out = []
    for _ in range(n):
        q -= 1
        if q < 0:
            q, y = 3, y - 1
        out.append(f"CY{y}Q{q + 1}I")
    return out  # newest first


def _duration_quarters(today, n=QUARTER_FRAMES):
    """The quarterly duration frames to read, newest first, the quarter still open included (QUARTERLY)."""
    y, q = today.year, (today.month - 1) // 3
    out = []
    for _ in range(n):
        out.append(f"CY{y}Q{q + 1}")
        q -= 1
        if q < 0:
            q, y = 3, y - 1
    return out


def _quarter_facts(jobs, frames):
    """({cik: {(start, end): {tag: {"val": value, "accn": the filing's accession number}}}} for every three-month period
    in the quarterly frames (QUARTERLY), the metrics left out for some quarter). A frame that failed is taken from the
    copy the last run kept in QUARTER_CACHE, which then keeps this run's; one with no copy leaves its metric out for that
    quarter for every company, since a missing tag would silently change which tag wins for some."""
    key = lambda job: f"{job[4]}|{job[6]}"
    try:
        with open(QUARTER_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    kept, reused, lost = {}, [], set()
    for job, frame in zip(jobs, frames):
        if frame is not None:
            kept[key(job)] = {str(cik): [d["val"], d["start"], d["end"], d.get("accn")] for cik, d in frame.items()
                              if d.get("start") and d.get("end") and d.get("val") is not None}
        elif key(job) in cache:
            kept[key(job)] = cache[key(job)]
            reused.append(key(job).replace("|", " "))
        else:
            lost.add((job[1], job[6]))
    out = {}
    for job in jobs:
        if (job[1], job[6]) in lost:
            continue
        for cik, (val, start, end, *accn) in kept[key(job)].items():
            out.setdefault(int(cik), {}).setdefault((start, end), {})[job[4]] = {"val": val,
                                                                                  "accn": accn[0] if accn else None}
    if reused:
        print(f"  {len(reused)} quarterly frames failed and were taken from the last run: " + ", ".join(reused),
              flush=True)
    if lost:
        print("  Quarterly figures left blank for every company after SEC request failures: "
              + ", ".join(sorted(f"{m} {p}" for m, p in lost)), flush=True)
    if len(reused) < len(jobs):
        try:
            os.makedirs(os.path.dirname(QUARTER_CACHE), exist_ok=True)
            tmp = QUARTER_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(kept, fh, separators=(",", ":"))
            os.replace(tmp, QUARTER_CACHE)
        except OSError:
            pass
    return out, {m for m, _ in lost}


def _quarter_gaps(c, since):
    """[(first day, last day)] of each gap between two of a company's quarters with revenue (fundamentals record `c`)
    that ends after `since`, other than a fourth quarter of a fiscal year in its annual figures, which is worked out
    from the year instead (_quarter_series)."""
    D, day = dt.date.fromisoformat, dt.timedelta(days=1)
    ps = sorted((D(a), D(b)) for (a, b), facts in (c.get("quarters") or {}).items()
                if QUARTER_DAYS[0] <= (D(b) - D(a)).days + 1 <= QUARTER_DAYS[1] and REVENUE_TAGS & facts.keys())
    ends = {D(s["end"]) for s in (c.get("annual") or {}).values() if s.get("end") and s.get("revenue") is not None}
    gaps = []
    for (_, b1), (a2, b2) in zip(ps, ps[1:]):
        gap = (a2 - b1).days - 1
        if b2 < since or gap <= QUARTER_DAYS[0] // 2:
            continue
        if gap <= QUARTER_DAYS[1] and any(abs((a2 - day - e).days) <= SEAM_DAYS for e in ends):
            continue
        gaps.append((b1 + day, a2 - day))
    return gaps


# The tags a company's whole set of facts (companyfacts) is kept for when its answer for one tag comes back empty
# (_tag_figures): every duration tag, ratio and share count the frames are read for, which covers every tag looked up
# in a company's own filings (its quarters' tags, one-time items, revenue, a bank's lines and net income).
FACTS_TAGS = {t for tags in (*DURATION.values(), *RATIOS.values(), *GROSS.values()) for t in tags} | {
    "WeightedAverageNumberOfDilutedSharesOutstanding"}
_facts_lock = threading.Lock()


def _company_facts(cik, memo={}):
    """{tag: {unit: [figures]}} for FACTS_TAGS from a company's whole set of facts (companyfacts, one larger request),
    read once a run."""
    with _facts_lock:
        if cik not in memo:
            try:
                facts = sec_json(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")["facts"]["us-gaap"]
            except NotFound:
                facts = {}
            memo[cik] = {t: facts[t].get("units") or {} for t in FACTS_TAGS if t in facts}
        return memo[cik]


def _tag_figures(cik, tag, units=("USD",)):
    """The figures the company's filings give for one tag (companyconcept, one request), in the first of `units` its
    answer has, or [] where it never tagged it. A tag is only looked up for a company the frames show using it, so an
    answer without figures is a wrong one: in September 2026 the SEC answered "USD": {} for about 120 of the tags
    looked up for companies' quarters and 27 of those looked up for their one-time items and net income, every time
    they were asked (Abbott Laboratories' revenue from customer contracts, 122 figures, and its net income; Pentair's
    write-downs of intangible assets; CTO Realty Growth's net income, which left its proxy's figure, $2.6M left to
    common shareholders, standing for its 10-K's $10.1M). The company's whole set of facts, which has them, is read
    instead, and where that has none either this raises, so the lookup counts as failed and is made again on the next
    run."""
    try:
        answer = sec_json(f"https://data.sec.gov/api/xbrl/companyconcept/CIK{cik:010d}/us-gaap/{tag}.json")["units"]
    except NotFound:
        return []
    pick = lambda got: next((got[u] for u in units if u in got), None)
    figures = pick(answer)
    if not isinstance(figures, list) or not figures:
        figures = pick(_company_facts(cik).get(tag) or {})
        if not isinstance(figures, list) or not figures:
            raise ValueError(f"no figures in the SEC's answers for {tag} of CIK {cik}")
    return figures


def _concept_rows(cik, tag, since):
    """[[start, end, value, accession number, date filed]] of every three-month and one-year figure ending on or after
    `since` that the company's 10-Q and 10-K filings (their amendments and transition reports; 20-F and 40-F for a
    foreign filer) give for one tag (_tag_figures), oldest filing first."""
    out = set()
    for f in _tag_figures(cik, tag):
        if f.get("start") and f.get("end") and f.get("accn") and f.get("val") is not None and f["end"] >= since \
                and f.get("form", "").startswith(("10-Q", "10-K", "20-F", "40-F")):
            days = (dt.date.fromisoformat(f["end"]) - dt.date.fromisoformat(f["start"])).days + 1
            if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1] or 350 <= days <= 380:
                out.add((f["start"], f["end"], f["val"], f["accn"], f.get("filed") or ""))
    return [list(r) for r in sorted(out, key=lambda r: (r[4], r[3], r[1], r[0]))]


def _footings(rows):
    """{accession number: footing} for the filings in `rows` ({tag: _concept_rows}): footings are numbered from 0 in the
    order the filings were made, and a filing starts a new one where it gives a figure for a period that differs by more
    than RESTATED_GAP from the figure the last filing before it gave for the same period and tag (QUARTER_FACTS). A
    restatement that reaches the comparatives one filing at a time (Aramark's 10-Qs for fiscal 2024, each giving its
    year-ago quarter without Vestis, spun off) so starts a footing with each, which leaves figures uncombined rather than
    ever combining two restatements: Freedom Holding's 10-K of June 2025 restated its 2024 revenue, and its 10-Qs from
    August 2025 its fiscal 2025 quarters again, beside that 10-K's year as first filed."""
    filings = {}
    for t, rs in rows.items():
        for start, end, val, accn, filed in rs:
            f = filings.setdefault(accn, [filed, []])
            f[0] = min(f[0], filed)
            f[1].append(((t, start, end), val))
    last, out, n = {}, {}, 0
    for accn, (_, facts) in sorted(filings.items(), key=lambda x: (x[1][0], x[0])):
        if any(k in last and abs(v - last[k]) > RESTATED_GAP * max(abs(v), abs(last[k])) for k, v in facts):
            n += 1
        out[accn] = n
        last.update(facts)
    return out


def _quarter_tags(c, nii_alone=False):
    """The tags a company's quarters are read from, which _quarter_lookups looks up: each fiscal year's revenue tag and
    the one each quarter would take first (DURATION), and a bank's net interest income and noninterest income tags where
    its quarters tag both, or with `nii_alone` (a REIT by its Nasdaq labels) the net interest income tag alone of a
    company that tags no revenue line in any year, whose revenue that is (derive's revenue_basis "net_interest")."""
    tags = [next(t for t in REVENUE_ORDER if t in facts) for facts in (c.get("quarters") or {}).values()
            if REVENUE_TAGS & facts.keys()]
    tags += [s["revenue_tag"] for s in (c.get("annual") or {}).values() if s.get("revenue_tag")]
    tags = [t for t in tags if t != STATEMENT_REVENUE]  # its rows come from the statements read, not a tag lookup
    alone = nii_alone and not tags
    for facts in (c.get("quarters") or {}).values():
        nii = next((t for t in DURATION["bank_nii"] + DURATION["bank_nii_alt"] if t in facts), None)
        if nii and DURATION["bank_noninterest_income"][0] in facts:
            tags += [nii, DURATION["bank_noninterest_income"][0]]
        elif nii and alone:
            tags.append(nii)
    return list(dict.fromkeys(tags))


def _frame_accns(c, tag):
    """The filings the frames took a company's figures under `tag` from, for its quarters and fiscal years."""
    out = {facts[tag].get("accn") for facts in (c.get("quarters") or {}).values() if tag in facts}
    out |= {({**(s.get("revenue_accns") or {}), **(s.get("gross_accns") or {})}).get(tag)
            for s in (c.get("annual") or {}).values()}
    return out - {None}


def _gave(rows, start, end, accn, revenue):
    """Whether one filing (accession number `accn`) gave `revenue` for a period, within RESTATED_GAP, under any revenue
    tag in a company's own filings ("concept"): Costco's 10-Q for its third quarter of fiscal 2026 gave the year-ago
    quarter's revenue only as revenue from customer contracts, beside the cost of sales the frames take from it."""
    return any(r[3] == accn and (r[0], r[1]) == (start, end) and abs(r[2] - revenue) <= RESTATED_GAP * abs(revenue)
               for t, rs in rows.items() if t in REVENUE_TAGS for r in rs)


def _check_gross_filings(companies):
    """Keeps a fiscal year's gross profit from another filing than its revenue (_gross_profit's gross_accn) only where
    that filing gave the same revenue (within RESTATED_GAP), as the company's own filings show ("concept",
    _quarter_lookups): Caterpillar's 2025 revenue in the frames comes from a later filing than its cost of sales, with
    the same figure. Otherwise, or where that can't be seen, the year has no gross profit."""
    dropped = 0
    for c in companies.values():
        for s in (c.get("annual") or {}).values():
            accn = s.pop("gross_accn", None)
            if accn is None or s.get("gross_profit") is None:
                continue
            if not _gave(c.get("concept") or {}, s.get("start"), s.get("end"), accn, s["revenue"]):
                s.update(gross_profit=None, gross_basis=None, gross_why="unknown")
                dropped += 1
    if dropped:
        print(f"  {dropped} fiscal years' gross profit left out, from another filing than their revenue that gave "
              "another revenue figure or couldn't be checked", flush=True)


def _quarter_lookups(companies, today, listed=None, minutes=QUARTER_LOOKUP_MINUTES, lookups=True, reits=frozenset()):
    """Looks up the tags each listed company's quarters are read from (_quarter_tags) in its own 10-Q and 10-K filings,
    as QUARTER_FACTS describes, and keeps what it has for each on the company: "concept" ({tag: _concept_rows}) and
    "unmatched" (the filings the frames took its figures from that its rows lacked when they were looked up). `listed`
    is {cik: market value}, a set of CIKs, or None for every company, and `reits` the CIKs whose net interest income
    alone may be their revenue (_quarter_tags). Lookups not made this run (the SEC throttled, the time ran out, or
    `lookups` is off) leave the rows kept from an earlier run, if any.

    Then fills the gaps in the quarters of a company whose frames leave one (_quarter_gaps) with the periods its own
    filings give under its revenue tags and its gross profit tag, and adds the fiscal years the annual frames lack
    ("fy_extra", {(start, end): {tag: {"val", "accn"}}}), which only work out a fourth quarter (_quarter_series). A
    period the frames have keeps the frames' figures, and one overlapping them is left out."""
    try:
        with open(QUARTER_FACTS, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    D, day = dt.date.fromisoformat, dt.timedelta(days=1)
    oldest = _duration_quarters(today)[-1]
    since = dt.date(int(oldest[2:6]), 3 * int(oldest[-1]) - 2, 1)
    rows_since = (since - 400 * day).isoformat()
    value = listed if isinstance(listed, dict) else {}
    want, refresh, seed = {}, [], []
    for cik, c in companies.items():
        if listed is not None and cik not in listed or not c.get("quarters"):
            continue
        gaps = _quarter_gaps(c, since)
        years = [s for _, s in sorted((c.get("annual") or {}).items()) if s.get("revenue_tag")]
        basis = next((s["gross_basis"] for s in reversed(years) if s.get("gross_basis")), None)
        tags = _quarter_tags(c, cik in reits) + ([basis] if basis else [])
        want[cik] = (list(dict.fromkeys(tags)), gaps)
        for t in want[cik][0]:
            e = cache.get(f"{cik}|{t}")
            if e is None or "rows" not in e:
                seed.append((cik, t))
                continue
            known = {r[3] for r in e["rows"]}
            unmatched = e.get("unmatched") or {}
            shown = _frame_accns(c, t)
            if shown - known - unmatched.keys() or today >= D(e["checked"]) + QUARTER_RECHECK_DAYS * day and any(
                    a in shown and D(first) + QUARTER_UNMATCHED_DAYS * day >= today for a, first in unmatched.items()):
                refresh.append((cik, t))
    seed.sort(key=lambda ct: -(value.get(ct[0]) or 0))  # the most valuable companies first
    todo = (refresh + seed) if lookups else []
    if len(seed) > QUARTER_SEED_TAGS:
        minutes = max(minutes, QUARTER_SEED_MINUTES)
    stop, blocked = time.monotonic() + minutes * 60, threading.Event()

    def look(ct):
        if blocked.is_set() or time.monotonic() > stop:
            return None
        try:
            return _concept_rows(*ct, rows_since)
        except Throttled:
            blocked.set()
        except Exception:  # one bad response shouldn't cost the rest; the next run tries again
            pass
        return None

    looked = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (cik, t), rows in zip(todo, pool.map(look, todo)):
            if rows is not None:
                old = (cache.get(f"{cik}|{t}") or {}).get("unmatched") or {}
                missing = _frame_accns(companies[cik], t) - {r[3] for r in rows}
                cache[f"{cik}|{t}"] = {"checked": today.isoformat(), "rows": rows,
                                       "unmatched": {a: old.get(a, today.isoformat()) for a in sorted(missing)}}
                looked += 1
    if looked:
        try:
            os.makedirs(os.path.dirname(QUARTER_FACTS), exist_ok=True)
            tmp = QUARTER_FACTS + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, separators=(",", ":"))
            os.replace(tmp, QUARTER_FACTS)
        except OSError:
            pass
    added = unchecked = 0
    for cik, (tags, gaps) in want.items():
        c = companies[cik]
        entries = {t: cache[f"{cik}|{t}"] for t in tags if "rows" in (cache.get(f"{cik}|{t}") or {})}
        c["concept"] = {t: e["rows"] for t, e in entries.items()}
        c["unmatched"] = {a for e in entries.values() for a in e.get("unmatched") or {}}
        unchecked += len(entries) < len(tags)
        if not gaps:
            continue
        q, extra = c.setdefault("quarters", {}), c.setdefault("fy_extra", {})
        spans = [(D(a), D(b)) for a, b in q]
        fys = [(D(s["start"]), D(s["end"])) for s in c["annual"].values() if s.get("start") and s.get("end")]
        for t, rows in c["concept"].items():
            latest = {}
            for start, end, val, accn, _ in rows:
                latest[(start, end)] = (val, accn)  # the latest filing wins, as in a frame
            for (start, end), (val, accn) in sorted(latest.items()):
                a, b = D(start), D(end)
                if b < since:
                    continue
                if (b - a).days + 1 >= 350:
                    if not any(a <= y1 - SEAM_DAYS * day and b >= y0 + SEAM_DAYS * day for y0, y1 in fys):
                        extra.setdefault((start, end), {})[t] = {"val": val, "accn": accn}
                elif (start, end) in q:
                    if t not in q[(start, end)]:
                        q[(start, end)][t] = {"val": val, "accn": accn}
                        added += 1
                elif not any(a <= s1 - SEAM_DAYS * day and b >= s0 + SEAM_DAYS * day for s0, s1 in spans):
                    q[(start, end)] = {t: {"val": val, "accn": accn}}
                    spans.append((a, b))
                    added += 1
    print(f"  Quarterly revenue tags of {len(want)} listed companies: {looked} of {len(todo)} lookups due were made "
          f"({len(refresh)} for new filings)" + (" (the SEC throttled the rest)" if blocked.is_set() else "")
          + ("" if lookups else " (none this run, since a quarterly revenue frame failed)")
          + f", {unchecked} companies still to look up; {sum(1 for w in want.values() if w[1])} had gaps in their "
          f"quarterly frames, {added} quarterly figures added", flush=True)


def _frame(taxonomy, tag, unit, period):
    try:
        data = sec_json(f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json")["data"]
    except NotFound:
        return {}  # nobody reported this line item for the period
    return {d["cik"]: d for d in data}


def _fetch_all(jobs):
    """Every job's frame, or None where it kept failing. After a pause, failures get one more sequential try."""
    blocked = threading.Event()  # once the SEC blocks us, stop asking until the retry pass
    errors = []

    def one(job):
        if blocked.is_set():
            return None
        try:
            return _frame(*job[3:])
        except Throttled:
            blocked.set()
        except Exception as e:  # a timeout, dropped connection or server error on one of many requests
            errors.append(f"{job[4]} {job[6]}: {e!r}")
        return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        frames = list(pool.map(one, jobs))
    todo = [i for i, f in enumerate(frames) if f is None]
    if todo:
        why = "the SEC is throttling" if blocked.is_set() else f"first error {errors[0]}"
        print(f"  {len(todo)} of {len(jobs)} SEC frame requests failed ({why}); retrying in {RETRY_PAUSE}s", flush=True)
        time.sleep(RETRY_PAUSE)
        blocked.clear()
        misses = 0
        for i in todo:
            frames[i] = one(jobs[i])
            misses = 0 if frames[i] is not None else misses + 1
            if blocked.is_set() or misses >= 5:
                break  # still down, so don't spend minutes on every remaining request
    return frames


def _tax_frames(jobs, frames):
    """`frames` with the tax rate reconciliation frames (TAX_EVIDENCE) that failed taken from the copy the last run
    kept in TAX_CACHE, which then keeps this run's. A past year's frame changes only as late filers add to it, so an
    old copy beats leaving every company without that year's evidence. Only the values are kept, all _items() reads."""
    return _kept_frames(jobs, frames, TAX_EVIDENCE, TAX_CACHE, "tax rate reconciliation")[0]


def _kept_frames(jobs, frames, metrics, path, what, filed=None):
    """`frames` with those of `metrics` that failed taken from the copy the last run kept in `path`, which then keeps
    this run's (_tax_frames), and the companies that had filed for each period a frame was taken for, as
    {(metric, year): set of CIKs, or None where unknown}. Only the values are kept, and the companies in the frames of
    `filed`, a metric every company that files for a period tags. A company that files after the copy was kept is then
    missing from it, and without that list would read as tagging nothing (no stock-based pay, no interest paid)."""
    key = lambda job: "|".join(job[4:7])
    try:
        with open(path, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    out, kept, reused, stale = list(frames), {}, [], {}
    if filed:
        filers, gaps = {}, set()
        for job, frame in zip(jobs, frames):
            if job[1] == filed:
                if frame is None:
                    gaps.add(job[6])
                else:
                    filers.setdefault(job[6], set()).update(frame)
        for p in {job[6] for job in jobs if job[1] == filed}:
            # A period one of whose frames failed would leave some filers out, so the last run's list is kept.
            if p in filers and p not in gaps:
                kept["filed|" + p] = sorted(filers[p])
            elif "filed|" + p in cache:
                kept["filed|" + p] = cache["filed|" + p]
    for n, (job, frame) in enumerate(zip(jobs, frames)):
        if job[1] not in metrics:
            continue
        if frame is not None:
            kept[key(job)] = {str(cik): d["val"] for cik, d in frame.items()}
        elif key(job) in cache:
            kept[key(job)] = cache[key(job)]
            out[n] = {int(cik): {"val": v} for cik, v in cache[key(job)].items()}
            reused.append(f"{job[4]} {job[6]}")
            if filed:
                known = cache.get("filed|" + job[6])
                stale[(job[1], job[2])] = None if known is None else set(known)
    if reused:
        print(f"  {len(reused)} {what} frames failed and were taken from the last run: " + ", ".join(reused),
              flush=True)
    if kept:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(kept, fh, separators=(",", ":"))
            os.replace(tmp, path)
        except OSError:
            pass
    return out, stale


def load_fundamentals(today=None, listed=None, reits=frozenset()):
    """Returns {"years": [...], "companies": {cik: {"annual": {year: {...}}, "latest": {...}, "loc": "US-CA", ...}}}.
    `listed` ({cik: market value}, or a set of CIKs) limits the lookups of quarterly figures in the companies' own
    filings (_quarter_lookups) to those companies, the most valuable first, and `reits` names the REITs among them
    (whose net interest income alone may be their revenue).

    Raises RuntimeError when the SEC can't supply the newest years' income or cash flow statements or any
    recent balance sheet, rather than publishing figures that would quietly fall back a year for everyone.
    """
    today = today or dt.date.today()
    years = _years(today)
    quarters = _quarters(today)

    jobs = []  # (section, metric, year or quarter, taxonomy, tag, unit, frame period)
    for metric, tags in DURATION.items():
        for y in years:
            jobs += [("annual", metric, y, "us-gaap", t, "USD", f"CY{y}") for t in tags]
    for metric, tags in RATIOS.items():
        for y in years:
            jobs += [("annual", metric, y, "us-gaap", t, "pure", f"CY{y}") for t in tags]
    # The working capital changes of the WC_HISTORY years before these, which _one_off_cash measures a year's against.
    for y in range(years[0] - WC_HISTORY, years[0]):
        jobs += [("history", "working_capital", y, "us-gaap", t, "USD", f"CY{y}") for t in DURATION["working_capital"]]
    for y in years[-3:]:  # three years, so companies whose newest year isn't filed yet still get a dilution figure
        jobs.append(("annual", "diluted_shares", y, "us-gaap", "WeightedAverageNumberOfDilutedSharesOutstanding", "shares", f"CY{y}"))
    for q in quarters:
        jobs += [("instant", metric, q, "us-gaap", t, "USD", q) for metric, tags in INSTANT.items() for t in tags]
        jobs.append(("instant", "shares_out", q, "dei", "EntityCommonStockSharesOutstanding", "shares", q))
        jobs.append(("instant", "bank_shares", q, "us-gaap", BANK_SHARES, "shares", q))
    for metric, tags in {**GROSS, **OVERHEAD, **INSURANCE, **AMORTIZATION}.items():
        for y in years:
            jobs += [("annual", metric, y, "us-gaap", t, "USD", f"CY{y}") for t in tags]
    # Quarterly frames, fetched with the rest so a pause for the SEC's limits covers them too, but kept apart: one that
    # fails never stops the run (_quarter_facts).
    qjobs = [("quarter", metric, p, "us-gaap", t, "USD", p) for metric, tags in QUARTERLY.items()
             for p in _duration_quarters(today) for t in tags]
    fetched = _fetch_all(jobs + qjobs)
    qfacts, qlost = _quarter_facts(qjobs, fetched[len(jobs):])
    frames, stale = _kept_frames(jobs, _tax_frames(jobs, fetched[:len(jobs)]), CASH_FLOW_METRICS, CASH_FLOW_CACHE,
                                 "cash-flow model", filed="ocf")

    failed = {(j[1], j[2]) for j, f in zip(jobs, frames) if f is None}
    bad_quarters = {p for m, p in failed if p in quarters and m not in OPTIONAL_INSTANT}
    critical = sorted(f"{m} CY{p}" for m, p in failed if m in CRITICAL and p in years[-2:])
    if critical or len(bad_quarters) == len(quarters):
        raise RuntimeError("SEC financial statements unavailable after retries: "
                           + (", ".join(critical) or "every recent balance sheet"))
    if failed:
        # A metric with any failed tag is left blank for that period, since a missing tag would silently
        # change which tag wins (or what gets summed) for some companies.
        print("  WARNING: left blank for every company after SEC request failures: "
              + ", ".join(sorted(f"{m} {p if p in quarters else f'CY{p}'}" for m, p in failed))
              + (f". Balance sheets skip {', '.join(sorted(bad_quarters))}." if bad_quarters else ""), flush=True)

    annual, inst, shares, info, history = {}, {}, {}, {}, {}
    for (section, metric, period, _tax, tag, _unit, _p), frame in zip(jobs, frames):
        if frame is None or (metric, period) in failed or (section == "instant" and metric not in OPTIONAL_INSTANT and period in bad_quarters):
            continue
        for cik, d in frame.items():
            if section == "annual":
                annual.setdefault(cik, {}).setdefault(period, {})[tag] = d
            elif section == "history":
                history.setdefault(cik, {})[period] = d["val"]
            elif metric == "shares_out":
                shares.setdefault(cik, {})[period] = (d["val"], d.get("end"))
            else:
                inst.setdefault(cik, {}).setdefault(period, {})[tag] = d
            if metric in TAX_EVIDENCE or metric in REIT_METRICS or metric in REIT_INSTANT or metric in BANK_METRICS \
                    or metric in BDC_METRICS or metric in BANK_INSTANT or metric == "bank_shares" or metric in CASH_FLOW_METRICS \
                    or metric in DEBT_CHECK_INSTANT or metric in GROSS or metric in OVERHEAD or metric in INSURANCE \
                    or metric in AMORTIZATION:
                # A company that files few tax tags could otherwise take its location from an old filing, and the REIT
                # and bank lines would move other companies' locations (SunPower's from California to New York).
                continue
            c = info.setdefault(cik, {})
            if d.get("loc") and d["loc"] != "-" and (section == "annual" or "loc" not in c):
                c["loc"] = d["loc"]  # each metric's annual frames run oldest year first, so a recent filing's location wins
            c.setdefault("name", d.get("entityName"))

    _check_items(annual)  # first, since the net income check compares against the revenue's filing
    reported = _check_net_income(annual)
    _check_bank_items(annual, reported)
    unknown = {m for m, _ in failed if m in ITEMS}
    if unknown:
        print("  One-time item frames incomplete, so write-downs, sales, securities and debt payoffs are not "
              "adjusted in this run", flush=True)
    tax_unknown = {(m, p) for m, p in failed if m in TAX_EVIDENCE}
    for what, ys in (("so no company's normal tax rate is read from", {p for m, p in tax_unknown if m != "etr"}),
                     ("so reported tax rates are not checked for", {p for m, p in tax_unknown if m == "etr"})):
        if ys:
            print(f"  Tax rate reconciliation frames missing, {what} {', '.join(f'CY{p}' for p in sorted(ys))} in this "
                  "run", flush=True)
    bank_failed = {p for m, p in failed if m in BANK_INSTANT}  # quarters whose tangible book value is unknown
    lease_failed = {p for m, p in failed if m in DEBT_CHECK_INSTANT}  # quarters whose lease liabilities are unknown
    companies = {}
    for cik, c in info.items():
        facts = annual.get(cik, {})
        has_capex = any(_capex(fy) is not None for fy in facts.values())
        def year(y, extra=frozenset()):
            return _annual(facts.get(y, {}), has_capex, ("capex", y) in failed, reported.get((cik, y), False),
                           unknown | extra | {m for m, p in tax_unknown if p == y}
                           | {m for m, p in failed if p == y
                              and (m in REIT_METRICS or m in BANK_METRICS or m in BDC_METRICS
                                   or m in CASH_FLOW_METRICS
                                   or m in GROSS or m in OVERHEAD or m in INSURANCE or m in AMORTIZATION)}
                           # A cash-flow model figure from the last run's copy of a frame (_kept_frames) is
                           # unknown for a company that filed since.
                           | {m for (m, p), known in stale.items()
                              if p == y and (known is None or cik not in known)})
        c["annual"] = {y: year(y) for y in years}
        # Total investment income is revenue only for a business development company, whose newest year with a revenue
        # figure is read from it. A company that has since tagged its revenue otherwise keeps its older years without
        # it, so its growth never compares two readings (Sui Group's 2022 and 2023, from when it lent as Mill City
        # Ventures, beside its revenue as a digital asset holder since).
        newest = next((a for _, a in sorted(c["annual"].items(), reverse=True) if a.get("revenue_tag")), None)
        not_bdc = bool(newest) and newest["revenue_tag"] != INVESTMENT_INCOME
        if not_bdc:
            for y in [y for y, a in c["annual"].items() if a.get("revenue_tag") == INVESTMENT_INCOME]:
                c["annual"][y] = year(y, {NOT_BDC})
        c["latest"] = _balance_sheet(inst.get(cik, {}), quarters, bank_failed, lease_failed)
        s = next((shares[cik][q] for q in quarters if q in shares.get(cik, {})), None)
        if s is not None:
            # The cover page's count and its date, which build.py checks for stock splits since.
            c["latest"]["shares_out"], c["latest"]["shares_as_of"] = s
        if history.get(cik):
            c["wc_history"] = history[cik]  # {year: change in working capital} before `years` (_one_off_cash)
        if qfacts.get(cik):
            c["quarters"] = qfacts[cik]  # {(start, end): {tag: value}} for each three-month period (_quarter_series)
            if not_bdc:
                # Nor do its quarters take their revenue from it (a quarter that has nothing else is left out).
                c["quarters"] = {k: {t: v for t, v in q.items() if t != INVESTMENT_INCOME} for k, q in c["quarters"].items()
                                 if q.keys() - {INVESTMENT_INCOME}}
                if not c["quarters"]:
                    del c["quarters"]
        companies[cik] = c
    # The registrants listings moved from are looked up with them (SUCCESSORS). A revenue frame lost this run leaves
    # that quarter out for every company: a gap the SEC's frames will fill again, not thousands of companies' to look up
    # one by one.
    if listed is not None:
        listed = dict(listed) if isinstance(listed, dict) else dict.fromkeys(listed, 0)
        listed.update({old: listed[new] for new, old in SUCCESSORS.items() if new in listed})
    _quarter_lookups(companies, today, listed, lookups="revenue" not in qlost, reits=reits)
    _check_gross_filings(companies)
    for new, old in SUCCESSORS.items():
        if old in companies:
            companies[new] = _successor(companies.get(new), companies[old])
    return {"years": years, "companies": companies}


# Listings whose company moved to a new SEC registrant (a holding company put on top by an 8-K12B filing), mapped to
# the registrant that filed the annual reports: {new CIK: old CIK}. The listed ticker points to the new one, which has
# only filed quarterly reports so far, so without this the company would have no financial figures at all. Exxon Mobil
# moved under ExxonMobil Holdings Corp (8-K12B of July 1, 2026; the old registrant filed to delist on July 2), whose
# first 10-Q (August 3, 2026) is its only filing with figures. Once the new registrant files an annual report of its
# own, its years take over (_successor), so an entry can stay until then.
SUCCESSORS = {2115436: 34088}


def _successor(new, old):
    """A company record for a listing whose registrant changed (SUCCESSORS): each fiscal year from the new registrant
    where it has one (revenue and net income), else from the old, and the newer of the two balance sheets and cover
    page share counts."""
    if not new:
        return old
    out = dict(new)
    out["annual"] = {y: s if s.get("revenue") is not None and s.get("net_income") is not None else
                     old.get("annual", {}).get(y, s) for y, s in (new.get("annual") or {}).items()}
    nl, ol = new.get("latest") or {}, old.get("latest") or {}
    latest = dict(nl if (nl.get("as_of") or "") >= (ol.get("as_of") or "") else ol)
    for k in ("shares_out", "shares_as_of"):
        latest.pop(k, None)
    counts = [x for x in (nl, ol) if x.get("shares_out")]
    if counts:
        newest = max(counts, key=lambda x: x.get("shares_as_of") or "")
        latest.update(shares_out=newest["shares_out"], shares_as_of=newest.get("shares_as_of"))
    out["latest"] = latest
    out["wc_history"] = {**(old.get("wc_history") or {}), **(new.get("wc_history") or {})}
    # Quarters from both, the new registrant's winning where both have one (its first 10-Q repeats the year-ago quarter),
    # and both registrants' own filings under each tag, which _footings orders by the date filed.
    out["quarters"] = {**(old.get("quarters") or {}), **(new.get("quarters") or {})}
    out["concept"] = {t: sorted((old.get("concept") or {}).get(t, []) + (new.get("concept") or {}).get(t, []),
                                key=lambda r: (r[4], r[3], r[1], r[0]))
                      for t in {**(old.get("concept") or {}), **(new.get("concept") or {})}}
    out["unmatched"] = (old.get("unmatched") or set()) | (new.get("unmatched") or set())
    return out


def _first(facts, tags):
    return next((facts[t] for t in tags if t in facts), None)


def _val(fact):
    return fact["val"] if fact else None


def _gross_profit(facts, rev, unknown=frozenset(), capex_failed=False):
    """{"gross_profit", "gross_basis", "gross_why"} for one fiscal year's facts, whose revenue fact is `rev`: the
    company's own gross profit line (basis "GrossProfit"), or its revenue less its cost of revenue (basis: the cost tag),
    both None where it can't be read soundly, and then why ("gross_why"): "insurer" (INSURANCE), "no_line" (neither a
    gross profit nor a cost of revenue line for the year), "partial" (a cost line that may be only part of the direct
    cost), or "unknown" (no revenue, the SEC frames failed, or figures that don't fit together).

    Every figure must cover the revenue's own dates. One from another filing than the revenue's is named
    ("gross_accn"), for _check_gross_filings to confirm that filing gave the same revenue, so a restated gross profit is
    never set against revenue as first filed or the reverse. The gross profit line must not exceed revenue,
    nor add up with a cost line to another revenue tag's figure far from this one (_off_footing). A cost line counts
    only when the company tags no gross profit line and operating income is there to show that the cost line is most
    of its costs (COST_SHARE_MIN) or that it and the company's overhead are nearly all of them (EXPLAINED), and not when
    it leaves out depreciation (DEPRECIATION_LEFT_OUT), which a failed capex frame (`capex_failed`, whose frames hold the
    depreciation) leaves unknown."""
    none = lambda why: {"gross_profit": None, "gross_basis": None, "gross_why": why, "gross_accn": None}
    if rev is None or not rev.get("val") or rev["val"] <= 0 or unknown & (set(GROSS) | set(INSURANCE)):
        return none("unknown")
    period = (rev.get("start"), rev.get("end"))
    same = lambda d: d is not None and (d.get("start"), d.get("end")) == period
    other = lambda d: d["accn"] if d.get("accn") and rev.get("accn") and d["accn"] != rev["accn"] else None
    insured = [facts[t]["val"] for tags in INSURANCE.values() for t in tags if same(facts.get(t))]
    if any(v >= INSURER_SHARE * rev["val"] for v in insured):
        return none("insurer")
    gp = facts.get("GrossProfit")
    if gp is not None:
        if same(gp) and gp["val"] <= rev["val"] and not _off_footing(facts, rev["val"], gp["val"], same):
            return {"gross_profit": gp["val"], "gross_basis": "GrossProfit", "gross_why": None,
                    "gross_accn": other(gp)}
        return none("unknown")
    costs = [facts[t] for t in GROSS["cost_of_revenue"] if same(facts.get(t)) and facts[t]["val"] >= 0]
    oi = facts.get("OperatingIncomeLoss")
    if not costs:
        return none("unknown" if any(t in facts for t in GROSS["cost_of_revenue"]) else "no_line")
    if not same(oi):
        return none("partial")
    cost = max(costs, key=lambda d: d["val"])
    spent = rev["val"] - oi["val"]
    if spent <= 0 or capex_failed:
        return none("unknown")
    line = lambda k: max([0] + [facts[t]["val"] for t in OVERHEAD[k] if same(facts.get(t))])
    overhead = None if unknown & set(OVERHEAD) else line("research") + max(line("sga"), line("selling") + line("admin"))
    if cost["val"] < COST_SHARE_MIN * spent:
        if not overhead or not EXPLAINED[0] <= (cost["val"] + overhead) / spent <= EXPLAINED[1]:
            return none("partial")
    if overhead:
        amortized = 0 if unknown & set(AMORTIZATION) else \
            max([0] + [facts[t]["val"] for t in AMORTIZATION["amortization"] if same(facts.get(t))])
        worn = max([0] + [facts[t]["val"] for t in DEPRECIATION if same(facts.get(t))]) - amortized
        left = spent - cost["val"] - overhead - amortized
        if worn >= DEPRECIATION_MIN * rev["val"] \
                and DEPRECIATION_LEFT_OUT[0] * worn <= left <= DEPRECIATION_LEFT_OUT[1] * worn:
            return none("partial")
    return {"gross_profit": rev["val"] - cost["val"],
            "gross_basis": next(t for t in GROSS["cost_of_revenue"] if facts.get(t) is cost), "gross_why": None,
            "gross_accn": other(cost)}


def _off_footing(facts, rev, gp, same=lambda d: d is not None):
    """Whether a period's gross profit line `gp` and a cost of revenue line in `facts` add up to another revenue tag's
    figure, more than FOOTING_GAP from its revenue `rev`, and so to no figure within 1% of it (see FOOTING_GAP). `same`
    tells which facts are for the period."""
    totals = [gp + facts[t]["val"] for t in GROSS["cost_of_revenue"] if same(facts.get(t))]
    if not totals or any(abs(x - rev) <= 0.01 * abs(rev) for x in totals):
        return False
    others = [facts[t]["val"] for t in DURATION["revenue"]
              if same(facts.get(t)) and abs(facts[t]["val"] - rev) > FOOTING_GAP * abs(rev)]
    return any(abs(x - o) <= 0.005 * abs(o) for x in totals for o in others)


def _revenue_tags(facts, unknown=frozenset()):
    """The revenue tags (DURATION) one fiscal year's figures may be read from: all of them, less a BDC's total
    investment income (INVESTMENT_INCOME) where the company tags premiums earned that year, as an insurer does, or
    where the premiums frame failed and that can't be told, or where its newer years tag revenue otherwise (NOT_BDC in
    `unknown`, load_fundamentals)."""
    if NOT_BDC in unknown or "premiums" in unknown or any(t in facts for t in INSURANCE["premiums"]):
        return DURATION["revenue"][:-1]
    return DURATION["revenue"]


def _annual(facts, has_capex, capex_failed, report, unknown=frozenset()):
    tags = _revenue_tags(facts, unknown)
    rev = _first(facts, tags)
    ocf = _val(_first(facts, DURATION["ocf"]))
    capex = _capex(facts)
    dep = _val(_first(facts, DEPRECIATION))
    if ocf is None or capex_failed:
        fcf = None
    elif capex is not None:
        fcf = ocf - capex
    elif has_capex or (dep and dep > 0.01 * abs(_val(rev) or 0)):
        # It reports capex in other years, or depreciation says it owns assets it must replace, so its capex
        # is under a tag this doesn't read. Operating cash flow alone would overstate free cash flow.
        fcf = None
    else:
        fcf = ocf
    ni = _net_income(facts, report)
    # From the net income tags a company without revenue tags uses, or a bank's net interest income (Truist tags
    # neither revenue nor NetIncomeLoss).
    period = rev or _first(facts, DURATION["net_income"] + DURATION["bank_nii"] + DURATION["bank_nii_alt"]) or {}
    return {
        "revenue": _val(rev),
        "net_income": ni,
        "operating_income": _val(facts.get("OperatingIncomeLoss")),
        "fcf": fcf,
        "diluted_shares": _val(facts.get("WeightedAverageNumberOfDilutedSharesOutstanding")),
        "end": period.get("end"),
        # The fiscal year's first day and the tag its revenue came from, which its quarters are matched on
        # (_quarter_series), and its gross profit (_gross_profit; None where it can't be read soundly).
        "start": period.get("start"),
        "revenue_tag": next((t for t in tags if t in facts), None),
        # Every revenue tag's figure for the same dates, which tells tags that agree apart (_quarter_series).
        "revenue_tags": {t: facts[t]["val"] for t in tags if t in facts and rev
                         and (facts[t].get("start"), facts[t]["end"]) == (rev.get("start"), rev["end"])},
        # The filing each revenue tag's figure (and a bank's net interest and noninterest income's) came from, which
        # tells whether it and the quarters share a footing (_quarter_series, _quarter_lookups).
        "revenue_accns": {t: facts[t].get("accn") for t in tags + DURATION["bank_nii"]
                          + DURATION["bank_nii_alt"] + DURATION["bank_noninterest_income"] if t in facts},
        "gross_accns": {t: facts[t].get("accn") for tags in GROSS.values() for t in tags if t in facts},
        **_gross_profit(facts, rev, unknown, capex_failed),
        "pretax": _val(_first(facts, PRETAX)),
        "pretax_has_equity_method": PRETAX[0] in facts,
        **{k: _val(_first(facts, DURATION[k])) for k in ("income_tax", "discontinued", "equity_method", "minority")},
        # The owners' own share, without what goes to minority holders (i3 Verticals' 2025: $14.2M of $20.9M).
        "discontinued_parent": _val(facts.get(DURATION["discontinued"][1])),
        # For REITs (_ffo). None where a frame they come from failed, which depreciation_failed tells apart from a
        # REIT that tags no depreciation (report.reit_kind).
        "depreciation": None if capex_failed or "reit_depreciation" in unknown else _val(_first(facts, REIT_DEPRECIATION)),
        "depreciation_failed": bool(capex_failed or "reit_depreciation" in unknown),
        # A business development company's net investment income (DURATION's bdc_nii), for the same dates as its
        # revenue, so a figure for another period never stands in; None where untagged or its frame failed.
        "nii": None if "bdc_nii" in unknown or not rev or "NetInvestmentIncome" not in facts
        or (facts["NetInvestmentIncome"].get("start"), facts["NetInvestmentIncome"]["end"]) != (rev.get("start"), rev["end"])
        else facts["NetInvestmentIncome"]["val"],
        "common_gap": None if "preferred" in unknown else _common_gap(facts, ni, _val(rev)),
        "preferred": None if "preferred" in unknown else _val(facts.get("PreferredStockDividendsIncomeStatementImpact")) or 0,
        # Whether net income is the whole business's, minority holders' share included (ProfitLoss), because the
        # company tags no net income of its own (_ffo).
        "ni_whole": "NetIncomeLoss" not in facts and "ProfitLoss" in facts,
        # The net income left to common shareholders as tagged, which a lender valued on its book value is measured on
        # however much preferred dividends take (derive's common_gap_reported).
        "ni_common": _val(facts.get("NetIncomeLossAvailableToCommonStockholdersBasic"))
        if "NetIncomeLoss" in facts or "ProfitLoss" in facts else None,
        # Operating cash flow, from continuing operations where the company tags that, which settles which reading of
        # a REIT's discontinued operations to trust (_ffo).
        "ocf": _val(_first(facts, DURATION["ocf"][::-1])),
        "dividends": None if "dividends" in unknown else max([0] + [_val(facts.get(t)) or 0 for t in DURATION["dividends"]]),
        "lease_income": _val(facts.get("OperatingLeaseLeaseIncome")),
        # For the cash-flow model: None where the frame failed, otherwise what the company tags (none reads as 0).
        # Negative readings (a year's reversed stock pay, a stray sign) count as none.
        **{k: None if k in unknown else max(0, _val(_first(facts, DURATION[k])) or 0)
           for k in ("interest_paid", "sbc", "construction_capex")},
        # Interest expense (0 where untagged), which report._cash_flow_model uses for interest paid where the company
        # tags no interest paid at all ("interest_paid_tagged"), rather than counting the interest as a cost while also
        # subtracting the debt it is paid on.
        "interest_expense": None if "interest_expense" in unknown else max(
            0, _val(_first(facts, DURATION["interest_expense"] + ["InterestExpense"])) or 0),
        "interest_paid_tagged": any(t in facts for t in DURATION["interest_paid"]),
        # One-off payments inside operating cash flow (derive's one_off_cash): None where unknown or untagged.
        "working_capital": None if "working_capital" in unknown else _val(facts.get(DURATION["working_capital"][0])),
        "software_capex": None if "software_capex" in unknown else max(
            [0] + [_val(facts.get(t)) or 0 for t in DURATION["software_capex"]]),
        **_bank_year(facts, unknown),
        **_items(facts, unknown),
    }


# The largest share of net interest income a bank's provision for loan losses can take before a net interest income
# worked out from a tagged total revenue is taken for a misread (_bank_year). Ally Financial's 2025 provision came to 24%.
BANK_PROVISION_MAX = 0.5


def _bank_year(facts, unknown=frozenset()):
    """A year's figures as a bank reports them, all None where they can't be read: revenue ("bank_revenue", net
    interest income before the provision for loan losses plus noninterest income, which is how banks and their analysts
    count it), its two parts, noninterest expense (the costs of running the bank) and the provision.

    Net interest income is tagged by nearly every bank, and is otherwise total interest income less interest expense,
    or net interest income after the provision plus the provision. Noninterest income a bank doesn't tag is what its
    pretax income implies (FVCB Bankcorp's 2025: $3.6M, as its 10-K shows). A bank that tags neither net interest
    income nor its parts can still give it through a tagged total revenue less noninterest income, when that leaves a
    provision between nothing and BANK_PROVISION_MAX of it (Ally Financial's 2025 total net revenue of $7.91B)."""
    v = lambda k: None if k in unknown else _val(_first(facts, DURATION[k]))
    nii, after, prov = v("bank_nii"), v("bank_nii_after_provision"), v("bank_provision")
    if nii is None:
        nii = v("bank_nii_alt")
    earned, paid = v("bank_interest_income"), v("bank_interest_expense")
    fees, costs, pretax = v("bank_noninterest_income"), v("bank_noninterest_expense"), _val(_first(facts, PRETAX))
    if nii is None and earned is not None and paid is not None:
        nii = earned - paid
    if nii is None and after is not None and prov is not None:
        nii = after + prov
    if after is None and nii is not None and prov is not None:
        after = nii - prov
    if fees is None and None not in (after, costs, pretax):
        fees = pretax - after + costs
    total = _val(_first(facts, ["Revenues", "RevenuesNetOfInterestExpense"]))
    if nii is None and None not in (total, fees, after) and 0 <= total - fees - after <= BANK_PROVISION_MAX * (total - fees):
        nii = total - fees
    return {
        "bank_revenue": nii + fees if nii is not None and fees is not None else None,
        "bank_nii": nii,
        "bank_noninterest_income": fees,
        "bank_noninterest_expense": costs,
        "bank_provision": nii - after if nii is not None and after is not None else prov,
        # Securities gains (positive) or losses under the bank's other tags (DURATION), which derive() reads for a bank
        # that tags none under SECURITIES. None where they contradict each other or the frames failed.
        "bank_securities": None if "bank_securities" in unknown else _group(facts, DURATION["bank_securities"]),
        "bank_goodwill_acquired": v("bank_goodwill_acquired"),
        "bank_preferred_dividends": None if "bank_preferred_dividends" in unknown else
        max([0] + [_val(facts.get(t)) or 0 for t in DURATION["bank_preferred_dividends"]]),
    }


def _common_gap(facts, ni, revenue=None):
    """The part of net income `ni`, as _net_income() chose it, that is not the common shareholders': preferred
    dividends, and minority holders' share where net income is read from ProfitLoss (Simon Property's 2025: $5.36B, of
    which $4.62B is its common shareholders'). From the net income left to common shareholders where the company tags
    it and the gap is plausible (at most COMMON_GAP_MAX of net income or sales, whichever is larger: Gladstone
    Commercial's 2025 preferred dividends took $12.7M of its $19.3M, and some REITs tag none of theirs), else from the
    tagged preferred dividends. Positive when common shareholders get less.

    Measured against the chosen net income, not the frame's: a frame can hold a proxy's figure that _net_income()
    replaced with the annual report's (Kilroy Realty's 2025: 302.64, unscaled, against $276.1M, all of it its common
    shareholders'). A common figure a thousand or a million times off is a scale slip, not a gap."""
    base = next((t for t in ("NetIncomeLoss", "ProfitLoss") if t in facts), None)
    if base is None or ni is None:
        return 0.0  # net income is already the common shareholders' own figure
    common = _val(facts.get("NetIncomeLossAvailableToCommonStockholdersBasic"))
    pref = _val(facts.get("PreferredStockDividendsIncomeStatementImpact")) or 0
    minority = (_val(facts.get("NetIncomeLossAttributableToNoncontrollingInterest")) or 0) if base == "ProfitLoss" else 0
    slip = common is not None and any(abs(common * k - ni) <= 0.02 * abs(ni) for k in (1e3, 1e6) if ni)
    # Beyond the minority share that ProfitLoss includes (Bluerock Homes' 2025: a $32.6M loss, $34.8M of it the
    # minority holders', and $13.7M of untagged preferred dividends).
    scale = COMMON_GAP_MAX * max(abs(ni), abs(common or 0), abs(revenue or 0))
    if common is not None and not slip and abs(ni - common - minority) <= scale:
        return ni - common
    return pref + minority


def _items(facts, unknown):
    """One year's possible one-time items. A tag the company did not use reads as zero, and None means unknown."""
    gw, other = _impairments(facts)
    pretax = _val(_first(facts, PRETAX))
    tax = _val(facts.get("IncomeTaxExpenseBenefit"))
    rate = tax / pretax if tax is not None and pretax else None
    lines_known = not unknown & (TAX_EVIDENCE - {"etr"})
    line = lambda key: _recon_line(facts, key, pretax, rate, lines_known)

    credits = max(abs(_val(facts.get(TAX_CREDITS[0])) or 0), sum(abs(_val(facts.get(t)) or 0) for t in TAX_CREDITS[1:]))
    if not credits and lines_known and pretax:
        pct = [_share(_val(facts.get(t)), rate) for t in RATIOS["tax_credits_pct"]]
        credits = max(abs(pct[0] or 0), sum(abs(v or 0) for v in pct[1:])) * abs(pretax)
    out = {
        "gw_impairment": gw,
        "impairment": other,
        "sale": _sale(facts),
        # For REITs (_reit_year): the asset sale tags on their own, and the other gains funds from operations leaves out.
        "sale_assets": _group(facts, SALE[4:]),
        "property_sale": _group(facts, PROPERTY_SALE),
        "reit_gain": None if "reit_gain" in unknown else sum(_val(facts.get(t)) or 0 for t in REIT_GAIN),
        "securities": _group(facts, SECURITIES),
        # Gains or losses on stakes in other companies under the broader tags (DURATION's equity_securities), which
        # derive() reads for a company other than a bank that tags none under SECURITIES; None where they contradict
        # each other or their frames failed.
        "other_securities": None if unknown & {"bank_securities", "equity_securities"} else _group(
            facts, DURATION["bank_securities"][:1] + DURATION["equity_securities"]),
        "debt_extinguishment": _val(facts.get("GainsLossesOnExtinguishmentOfDebt")) or 0,
        "gw_tax_rate": _goodwill_tax_rate(facts, gw),
        "valuation_allowance": line("valuation_allowance"),
        "tax_credits": credits,
        # Evidence for _tax_one_time() and _clean_rate(): the reconciliation lines that are often one-time
        # ({line: amount}, 0 where none is tagged) and the effective tax rate the company reports. None when their
        # frames could not be read for this year.
        "tax_lines": {k: line(k) or 0 for k in TAX_LINES} if lines_known else None,
        "etr": _val(facts.get(RATIOS["etr"][0])) if "etr" not in unknown else None,
    }
    if unknown & ITEMS:
        # Taking out gains while charges go unread (or the reverse) would tilt every company's earnings one way, so
        # a run missing any of these frames reads none of them. Discontinued operations, untagged gains and the tax
        # check still apply.
        out.update(gw_impairment=None, impairment=None, sale=None, sale_assets=None, property_sale=None, reit_gain=None,
                   securities=None, other_securities=None, debt_extinguishment=None, gw_tax_rate=None,
                   valuation_allowance=None, tax_credits=None)
    return out


def _share(value, rate):
    """A tax rate reconciliation line tagged as a share of pretax income, or None when it is on the wrong scale: over
    100% of pretax income while the company's tax rate (`rate`, tax over pretax income) is nowhere near that far from
    21% (Steris' 2024 disposal line: -3.71, meaning -3.71%, beside a 23.2% rate; Rackspace's 2024 research credits:
    29,200,000)."""
    if value is None or abs(value) <= 1 or rate is not None and abs(rate - FEDERAL_TAX) >= 0.5 * abs(value):
        return value
    return None


def _recon_line(facts, key, pretax, rate, pct_ok=True):
    """One line of the tax rate reconciliation in dollars, positive where it raised the tax bill, or None when the
    company tagged none of it: from the dollar tags, or else the percentage tags (_share) times pretax income. Where
    both are tagged and differ by more than a point of pretax income, the percentage counts, since it sits beside the
    effective tax rate that confirms the year's figures (Stryker tagged $162M of settlements in each of 2022 to 2024,
    while its percentages show none in 2023 and 2024, and a 6.1% benefit in 2022). The tags of a line are its pieces,
    summed, except in TAX_TOTALS, whose first tag is the total when tagged (RTX's 2024 settlements: $300M in all,
    $277M of it domestic)."""
    def combine(values):
        if key in TAX_TOTALS and values[0] is not None:
            return values[0]
        got = [v for v in (values[1:] if key in TAX_TOTALS else values) if v is not None]
        return sum(got) if got else None

    dollars = combine([_val(facts.get(t)) for t in DURATION[key]])
    share = combine([_share(_val(facts.get(t)), rate) for t in RATIOS[key + "_pct"]]) if pct_ok and pretax else None
    pct = share * pretax if share is not None else None
    if dollars is None or pct is not None and abs(dollars - pct) > 0.01 * abs(pretax):
        return pct
    return dollars


def _impairments(facts):
    """(goodwill, everything else) written down in one year. Charges are positive. Every reading of the other
    write-downs is a floor on the true total, since pieces may overlap, so the largest counts.

    The same charge is often tagged more than once. The broad AssetImpairmentCharges at least as large as the goodwill
    write-down is taken to include it (Darling's 2025: $57.8M, $19.0M of it goodwill). Any other reading within 1% of
    the goodwill write-down is that write-down tagged again (Chord Energy's 2025: $539.3M as both goodwill and oil and
    gas properties; Integra's 2025: $511.4M as both goodwill and finite-lived intangibles), and two halves of a pair
    that match are one charge tagged twice (Americold's 2025: $47.1M both as assets held for use and as assets to be
    disposed of). OtherAssetImpairmentCharges is sometimes the total and sometimes only the rest (New Fortress Energy's
    2025: $860.9M besides $598.1M of goodwill), so it counts as the total when it matches or exceeds a total that
    includes goodwill (Jack in the Box's 2025: $214.0M, against $209.6M of goodwill and intangibles and $4.4M of
    other assets) or exceeds the goodwill write-down by less than a tenth (AGCO's 2024: $369.5M of impairment
    charges, $354.1M of them goodwill).

    A write-down of goodwill or of intangible assets in use is never reversed under US accounting rules, so a negative
    figure there is a slipped sign (Cummins' 2025 goodwill: -$210M). Any other negative figure is not a write-down: the
    reversal of a loss on assets held for sale (Newmont's 2025: $1.07B), a line that nets write-downs against gains on
    selling businesses (Community Health Systems' 2025: a $406M net gain) or a misplaced running total (GEE Group's
    goodwill write-downs to date). None of those is a charge to add back, so they read as zero."""
    g = lambda t: 0 if t not in facts else abs(facts[t]["val"]) if t in NO_REVERSAL else max(facts[t]["val"], 0)
    gw = g("GoodwillImpairmentLoss")
    broad, gw_and_intangibles = g("AssetImpairmentCharges"), g("GoodwillAndIntangibleAssetImpairment")
    totals = [t for t in (broad, gw_and_intangibles) if gw and t >= gw]

    def net(x, total=False):
        if gw and (total or abs(x - gw) <= 0.01 * gw):
            return max(0.0, x - gw)
        return x

    def pair(a, b):
        a, b = net(g(a)), net(g(b))
        return max(a, b) if abs(a - b) <= 0.005 * max(a, b) else a + b

    other = g("OtherAssetImpairmentCharges")
    # TangibleAssetImpairmentCharges can be the total of every write-down, goodwill included, when it matches the
    # goodwill write-down plus the pieces tagged beside it (Hudson Pacific's 2025: $299.3M, of which $147.8M goodwill).
    tangible = g("TangibleAssetImpairmentCharges")
    pieces = sum(g(t) for t in IMPAIRMENT if t not in (
        "TangibleAssetImpairmentCharges", "GoodwillAndIntangibleAssetImpairment", "OtherAssetImpairmentCharges"))
    readings = [
        net(g("ImpairmentOfIntangibleAssetsExcludingGoodwill")),
        pair("ImpairmentOfIntangibleAssetsIndefinitelivedExcludingGoodwill", "ImpairmentOfIntangibleAssetsFinitelived"),
        pair("ImpairmentOfLongLivedAssetsHeldForUse", "ImpairmentOfLongLivedAssetsToBeDisposedOf"),
        net(tangible, total=bool(gw) and abs(tangible - gw - pieces) <= 0.02 * tangible),
        *(net(g(t)) for t in IMPAIRMENT[7:] if t not in ("OtherAssetImpairmentCharges", "TangibleAssetImpairmentCharges")),
        net(other, total=other >= gw and (other <= 1.1 * gw or any(other >= 0.995 * t for t in totals))),
        max(0.0, gw_and_intangibles - gw),
        broad - gw if broad >= gw else broad,
    ]
    return gw, max(readings)


def _group(facts, tags):
    """The largest gain (positive) or loss among `tags`, since a broad tag often includes a narrower one, or None when
    they point opposite ways: then one was tagged the wrong way round (U-Haul's 2025: -$104M and +$104M, Citigroup's
    securities gains) or they measure different things (Good Times' 2025: a $115K loss on disposals and a $469K
    gain), and neither can be trusted."""
    vals = [facts[t]["val"] for t in tags if facts.get(t, {}).get("val")]
    if not vals:
        return 0
    return None if min(vals) < 0 < max(vals) else max(vals, key=abs)


def _sale(facts):
    """Gain (positive) or loss on selling a business or assets, or None when the tags contradict each other. A
    business sale stands on its own when the asset tags point the other way, which is usually an ordinary disposal
    loss or a write-down (Ameresco's 2024: a $38.0M gain on selling a business, and $12.8M of write-downs and
    disposal losses)."""
    business, assets = _group(facts, SALE[:4]), _group(facts, SALE[4:])
    if not business:
        return assets if business == 0 else None
    return assets if assets and assets * business > 0 and abs(assets) > abs(business) else business


def _goodwill_tax_rate(facts, gw):
    """Tax saved per dollar of goodwill written down, where the filing says, else None. Goodwill write-downs are
    usually not deductible: Hewlett Packard Enterprise's 2025 tax reconciliation shows $330M of tax it could not
    save on its $1.58B write-down, which is 21% of the whole amount."""
    if not gw:
        return None
    net = _val(facts.get("GoodwillImpairmentLossNetOfTax"))
    if net is not None and 0.6 * gw <= abs(net) <= gw:  # a tax saving above 40% would be a misread figure
        return (gw - abs(net)) / gw
    lost = _val(facts.get("IncomeTaxReconciliationNondeductibleExpenseImpairmentLosses"))
    if lost is not None and lost > 0:
        return max(0.0, FEDERAL_TAX - lost / gw)
    return None


def _capex(facts):
    """Capital spending. Oil and gas producers report drilling spend on their own lines, next to ordinary
    property purchases. PaymentsToAcquireProductiveAssets is broader and already includes it."""
    v = lambda t: _val(facts.get(t))
    ppe, broad = v(CAPEX[0]), v(CAPEX[1])
    og = [x for x in (v(CAPEX[2]), v(CAPEX[3])) if x is not None]
    og = sum(og) if og else None
    if ppe is not None:
        return ppe + og if og is not None and og != ppe else ppe  # an equal figure is one line tagged twice
    if broad is not None or og is not None:
        return max(x for x in (broad, og) if x is not None)
    # Only when nothing else is reported: oil and gas property purchases, which are usually acquisitions, and "other"
    # property purchases, which some companies tag as their only capex line (CAPEX). The larger counts, as they can be
    # one line tagged twice.
    rest = [x for x in (v(CAPEX[4]), v(CAPEX[5])) if x is not None]
    return max(rest) if rest else None


NEEDS_REPORT = object()
_BANK_INTEREST = [t for k in ("bank_nii", "bank_nii_alt", "bank_interest_income", "bank_nii_after_provision")
                  for t in DURATION[k]]
_BANK_NONINTEREST = DURATION["bank_noninterest_income"] + DURATION["bank_noninterest_expense"]


def _bank_like(facts):
    """Whether a year's facts include a bank's income statement lines: net interest income or its parts, and
    noninterest income or expense. Other companies that tag a stray interest line are left as they were (ATIF Holdings'
    $26 of net interest income in 2024)."""
    return any(t in facts for t in _BANK_INTEREST) and any(t in facts for t in _BANK_NONINTEREST)


def _net_income(facts, report=False):
    """NetIncomeLoss, checked for proxy statements. A proxy's pay-versus-performance table is tagged NetIncomeLoss
    and filed after the annual report, so the frame shows it. It is often unscaled (thousands read as dollars) or
    shows a loss as a positive number, and it can predate a restatement in the annual report (Twin Disc's 2026
    proxy: $34.5M, its 10-K: $27.1M).

    A suspect figure returns NEEDS_REPORT while `report` is None. Otherwise `report` is the annual report's own
    figure, which replaces it, or False when that lookup found nothing.
    """
    ni = facts.get("NetIncomeLoss")
    if ni is None:
        return _val(facts.get("ProfitLoss") or facts.get("NetIncomeLossAvailableToCommonStockholdersBasic"))
    # A bank that tags no revenue has its net interest income as the scale reference instead (First Financial
    # Bancorp's 2026 proxy: 255,605 for 2025, in thousands, beside $642M of net interest income in its 10-K), though
    # not as a sign that net income came from the annual report, since a quarterly report can supply both (Colony
    # Bankcorp's first quarter of 2026, tagged as the year 2025).
    bank = _bank_like(facts) and _first(facts, DURATION["bank_nii"] + DURATION["bank_nii_alt"])
    rev = _first(facts, DURATION["revenue"])
    x, r = ni["val"], _val(rev) or _val(bank)
    if rev and ni["accn"] == rev["accn"]:
        return None if rev["val"] > 50e6 and abs(x) < 1e-4 * rev["val"] else x
    # A proxy's figure often includes minority holders' share: it matches the annual report's ProfitLoss, while that
    # report's own net income leaves the minority share out (Verizon's 2026 proxy: $17.61B for 2025, which is its 10-K's
    # $17.17B plus $434M to minority holders; DaVita's $1.08B against $747M; Tenet's $2.37B against $1.41B, whose 10-K
    # tags no NetIncomeLoss at all). Then the annual report's ProfitLoss less its minority share is the owners' own.
    pl, mi = facts.get("ProfitLoss"), facts.get("NetIncomeLossAttributableToNoncontrollingInterest")
    if pl and mi and mi["val"] and pl["accn"] != ni["accn"] and mi["accn"] == pl["accn"] \
            and (not rev or pl["accn"] == rev["accn"]) and abs(x - pl["val"]) <= 0.02 * abs(pl["val"]):
        return pl["val"] - mi["val"]
    # It came from another filing, so compare it with the annual report's own figures (from the filing that
    # reported revenue, or from any other filing when there is no revenue). A wrong scale is off by 1,000x.
    sane = lambda v: not (r and r > 10e6) or 1e-3 * r <= abs(v) <= 50 * r
    alts = [facts[t]["val"] for t in ("NetIncomeLossAvailableToCommonStockholdersBasic", "ProfitLoss")
            if t in facts and facts[t]["val"] and facts[t]["accn"] != ni["accn"] and (not rev or facts[t]["accn"] == rev["accn"])]
    # A lost minus sign shows against another filing's income before tax less the tax, or its operating income
    # when that is all it tags.
    pretax, op = _first(facts, PRETAX), facts.get("OperatingIncomeLoss")
    own = (pretax, pretax["val"] - (_val(facts.get("IncomeTaxExpenseBenefit")) or 0)) if pretax else (op, _val(op))
    flipped = own[0] is not None and own[0]["accn"] != ni["accn"] and x * own[1] < 0
    # A bank's net income is its pretax income less its tax, give or take a little (minority holders, sold businesses),
    # so one far from that in another filing is checked against the annual report (First Community Bankshares' 2025:
    # $12.0M from a quarterly report, against $48.8M in its 10-K, whose pretax income less tax gives $48.8M).
    far = bool(bank and pretax and pretax["accn"] != ni["accn"] and own[1]) and not 0.65 <= x / own[1] <= 1.5
    # Matching only one of them is not enough: a proxy often shows profit including minority holders' share,
    # which is ProfitLoss (Tenet's 2022: $1.0B, against the owners' $411M).
    if alts and all(abs(x - a) <= 0.02 * abs(a) for a in alts) or (not alts and sane(x) and not flipped and not far):
        return x
    # Preferred dividends and minority interests also set those tags apart, and income from sold businesses can
    # turn a pretax loss into a profit, so a gap short of a scale or sign error is checked against the annual
    # report, and stands when that lookup fails.
    same_scale = any(1 / 50 < x / a < 50 for a in alts) or (not alts and sane(x))
    if report is None:
        return NEEDS_REPORT
    if report is not False:
        return report
    return x if same_scale else next((a for a in alts if sane(a)), None)


def _report_net_income(cik, ends):
    """{fiscal year end: net income} from the company's own 10-K filings (_tag_figures), the latest filing winning."""
    facts = _tag_figures(cik, "NetIncomeLoss")
    out = {}
    for f in sorted(facts, key=lambda f: f.get("filed", "")):
        if f.get("form", "").startswith("10-K") and f.get("start") and f["end"] in ends:
            if (dt.date.fromisoformat(f["end"]) - dt.date.fromisoformat(f["start"])).days > 300:
                out[f["end"]] = f["val"]
    return out


def _check_net_income(annual):
    """{(cik, year): the annual report's net income, or False} for every frame figure that looks like a proxy's."""
    suspects = {}
    for cik, by_year in annual.items():
        for y, facts in by_year.items():
            if _net_income(facts, None) is NEEDS_REPORT:
                suspects.setdefault(cik, {})[y] = facts["NetIncomeLoss"]["end"]
    if not suspects:
        return {}
    blocked = threading.Event()

    def look(cik):
        if not blocked.is_set():
            try:
                return _report_net_income(cik, set(suspects[cik].values()))
            except Throttled:
                blocked.set()
            except Exception:
                pass
        return {}

    with ThreadPoolExecutor(max_workers=4) as pool:
        found = dict(zip(suspects, pool.map(look, suspects)))
    out = {(cik, y): found[cik].get(end, False) for cik, ys in suspects.items() for y, end in ys.items()}
    fixed = sum(1 for v in out.values() if v is not False)
    print(f"  {len(out)} net income figures came from a later filing and disagreed with the annual report; "
          f"{fixed} replaced with the 10-K's own figure" + (" (the SEC throttled the rest)" if blocked.is_set() else "")
          + ", the rest use the 10-K's other net income tags or stay blank", flush=True)
    return out


ITEM_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache", "item_facts.json")
ITEM_MINUTES = 20  # time budget for looking up one-time item and revenue figures; the rest wait for the next run
# Marks an item cache whose absences (None) all come from answers _tag_figures checked. Absences cached before that
# (September 2026) may be the SEC's empty answers rather than a 10-K without the figure, and only another lookup tells
# them apart, so a cache without the mark has them dropped and looked up again, once: in a copy of September 2026, 191
# figures took 157 lookups, 17 of which were such answers (Humana's $128M write-down of intangible assets in 2025).
ITEM_ABSENCES_CHECKED = "absences_checked"


def _check_items(annual, minutes=ITEM_MINUTES, flagged=None,
                 what="one-time item and revenue figures came from a filing other than an annual report"):
    """Replaces one-time item and revenue figures that came from a filing other than an annual report with the annual
    report's own figure, or drops them when no annual report has one. A frame shows the latest filing that tagged a
    year, which can be a proxy or a quarterly report with the year mislabelled or restated (Mobileye's 2026 proxy: its
    $2.7B 2024 goodwill write-down tagged as 2025's, when its annual report shows none in 2025; Xponential Fitness's
    2026 10-Q: $55.4M of 2025 goodwill write-downs, against $7.5M in its 10-K; Quinstreet's proxy, above). A revenue
    figure is only dropped when another revenue tag remains for that year, so a company never loses its sales figure.

    A figure counts as the annual report's when it came from a filing that supplied the company's pretax income, tax,
    operating income or operating cash flow for that year or the next two (whose annual reports repeat it). The
    others are looked up in the company's 10-K filings, one request per company and tag (_report_items), and the
    answers are kept in .cache/item_facts.json, since a filing never changes. Figures still unchecked when the SEC
    throttles, answers without figures (_tag_figures) or the time budget runs out stay as the frame has them, and the
    next run checks them. `flagged` ({(cik, tag): [(year, fact)]}) names other figures to check the same way instead
    (_check_bank_items), and `what` how the summary counts them."""
    if flagged is None:
        flagged = {}  # (cik, tag) -> [(year, fact)]
        for cik, by_year in annual.items():
            statements = {y: {d["accn"] for t, d in facts.items() if t in STATEMENT_TAGS} for y, facts in by_year.items()}
            for y, facts in by_year.items():
                ok = set().union(*(statements.get(y + k, set()) for k in range(3)))
                for t, d in facts.items():
                    if (t in ITEM_TAGS or t in REVENUE_TAGS) and d.get("val") and d.get("accn") not in ok:
                        flagged.setdefault((cik, t), []).append((y, d))
    if not flagged:
        return
    try:
        with open(ITEM_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
    except (OSError, ValueError):
        cache = {}
    if not cache.get(ITEM_ABSENCES_CHECKED):
        absent = sum(1 for v in cache.values() if v is None)
        cache = {k: v for k, v in cache.items() if v is not None}
        cache[ITEM_ABSENCES_CHECKED] = True
        print(f"  {absent} figures cached as absent from the 10-K are looked up again, as the SEC's empty answers "
              "were once taken for absences", flush=True)
    key = lambda cik, t, d: f"{cik}|{t}|{d['accn']}|{d.get('start')}|{d['end']}"
    todo = [ct for ct, facts in flagged.items() if any(key(*ct, d) not in cache for _, d in facts)]
    todo.sort(key=lambda ct: -max(y for y, _ in flagged[ct]))  # the newest years matter most
    stop = time.monotonic() + minutes * 60
    blocked = threading.Event()

    def look(ct):
        if blocked.is_set() or time.monotonic() > stop:
            return None
        try:
            return _report_items(*ct)
        except Throttled:
            blocked.set()
        except Exception:  # one bad response shouldn't cost the rest; the next run tries again
            pass
        return None

    looked = 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        for (cik, t), found in zip(todo, pool.map(look, todo)):
            if found is not None:
                looked += 1
                for _, d in flagged[(cik, t)]:
                    cache[key(cik, t, d)] = found.get((d.get("start"), d["end"]))
    if looked:
        try:
            os.makedirs(os.path.dirname(ITEM_CACHE), exist_ok=True)
            tmp = ITEM_CACHE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(cache, fh, separators=(",", ":"))
            os.replace(tmp, ITEM_CACHE)
        except OSError:
            pass
    fixed = dropped = unchecked = kept = 0
    for (cik, t), facts in flagged.items():
        for y, d in facts:
            k = key(cik, t, d)
            if k not in cache:
                unchecked += 1
            elif cache[k] is None:
                year = annual[cik][y]
                other = [r for r in REVENUE_TAGS if r != t and r in year and year[r].get("val")]
                if t in REVENUE_TAGS and not any(cache.get(key(cik, r, year[r]), 0) is not None for r in other):
                    kept += 1  # no other sales figure to fall back on
                    continue
                del year[t]
                dropped += 1
            else:
                # The annual report's own figure and filing, so _net_income() compares a net income figure with that
                # filing's (JAKKS Pacific's 2025 proxy: net income 1,000 times too large, next to a 10-Q's quarter
                # tagged as the year's sales). Cache entries from before the filing was kept are bare values.
                val, accn = cache[k] if isinstance(cache[k], list) else (cache[k], d["accn"])
                if val != d["val"] or accn != d["accn"]:
                    annual[cik][y][t] = dict(d, val=val, accn=accn)
                    fixed += val != d["val"]
    print(f"  {sum(len(f) for f in flagged.values())} {what}: {fixed} replaced with the 10-K's figure, {dropped} dropped "
          f"as absent from the 10-K ({kept} revenue figures kept for lack of another), {unchecked} unchecked"
          + (" (the SEC throttled)" if blocked.is_set() else "") + f"; {looked} lookups this run", flush=True)


# The largest share of a bank's revenue its net income can be before the year's bank figures are taken for a quarter
# tagged as the year (_check_bank_items). The most any bank came to on September 2026 data was 45% (a tax benefit).
BANK_NI_MAX = 0.6
BANK_ITEM_MINUTES = 5  # the time budget for _check_bank_items' lookups
# A bank's income statement lines that _check_bank_items verifies (every duration tag derive reads, and the diluted share
# count), but not NetIncomeLoss, which _check_net_income already has, nor the cash-flow model's, which a bank never
# gets.
BANK_CHECKED = ({t for k, tags in DURATION.items() if k not in CASH_FLOW_METRICS for t in tags}
                | {t for tags in RATIOS.values() for t in tags}
                | {"WeightedAverageNumberOfDilutedSharesOutstanding"}) - {"NetIncomeLoss"}
BANK_TAGS = {t for k, tags in DURATION.items() if k.startswith("bank_")
             and k not in ("bank_securities", "bank_goodwill_acquired", "bank_preferred_dividends") for t in tags}


def _check_bank_items(annual, reported, minutes=BANK_ITEM_MINUTES):
    """Checks a bank's figures against its annual report (_check_items) where they came from a filing that is not its
    annual report, which _check_items' test can't tell when the same filing also supplied the pretax income it trusts:
    - a filing whose net income for the year the annual report contradicted (`reported`, from _check_net_income):
      Pinnacle Financial's 8-K filed beside its 2025 10-K, which carried Synovus' statements for the two banks' merger
      (net interest income of $1.87B for 2025, against Pinnacle's own $1.55B), and Colony Bankcorp's 10-Q for the first
      quarter of 2026, whose figures were tagged as the year 2025's ($21.0M of net interest income, against $91.9M);
    - the filings behind a year whose net income comes to more than BANK_NI_MAX of its bank revenue, which only a
      quarter read as a year produces.
    Only for companies that tag a bank's income statement lines (_bank_like)."""
    flagged = {}
    for cik, by_year in annual.items():
        for y, facts in by_year.items():
            if not _bank_like(facts):
                continue
            ni, rep = facts.get("NetIncomeLoss"), reported.get((cik, y), False)
            suspect = {ni["accn"]} if ni is not None and rep is not False and rep != ni["val"] else set()
            revenue, income = _bank_year(facts)["bank_revenue"], _net_income(facts, rep)
            if revenue and revenue > 0 and income is not None and income > BANK_NI_MAX * revenue:
                suspect |= {facts[t]["accn"] for t in BANK_TAGS if t in facts}
            for t, d in facts.items():
                if t in BANK_CHECKED and d.get("accn") in suspect and d.get("val") is not None:
                    flagged.setdefault((cik, t), []).append((y, d))
    if flagged:
        _check_items(annual, minutes, flagged, "bank figures came from a filing that is not the annual report")


def _report_items(cik, tag):
    """{(start, end): [value, accession]} for one line item from the company's annual reports (10-K, or 20-F and 40-F
    for foreign companies), the latest filing winning. In dollars, or in shares or as a ratio for a tag with no
    dollars (_tag_figures)."""
    out = {}
    for f in sorted(_tag_figures(cik, tag, ("USD", "shares", "pure")), key=lambda f: f.get("filed", "")):
        if f.get("form", "").startswith(("10-K", "20-F", "40-F")) and f.get("start"):
            out[(f["start"], f["end"])] = [f["val"], f.get("accn")]
    return out


def _bank_parts(facts, known=True):
    """One quarter's balance sheet lines for a bank's tangible book value (_tangible_equity), or None without equity and
    total assets or when a frame they need failed (`known`): {"equity", "assets", "goodwill", "other", "preferred",
    "servicing", "lumped"}. Goodwill, other intangible assets and preferred stock are None where the company tagged none
    of their tags that quarter.

    Other intangible assets are the largest of their readings (a total, or its finite and indefinite-lived parts),
    without mortgage servicing rights, which banks count in tangible book value. A total nearer the parts plus the
    servicing rights tagged beside it than the parts alone includes them ("lumped": Huntington's June 2026 other
    intangible assets of $1.69B, $915M of them finite-lived and $752M servicing rights), one nearer the parts doesn't
    (Capital One's $16.1B, with $286M of servicing rights tagged apart), and with no parts tagged it can't be told
    (None) and stays whole. Goodwill with them is the larger of their sum and a tag for both together."""
    # Equity with any minority holders' share where that is all a bank tags (OceanFirst, Amalgamated), as for any company.
    equity, assets = _val(_first(facts, INSTANT["equity"])), _val(facts.get("Assets"))
    if equity is None or assets is None or not known:
        return None
    tagged = lambda tags: any(t in facts for t in tags)
    g = lambda t: max(_val(facts.get(t)) or 0, 0)
    parts = g("FiniteLivedIntangibleAssetsNet") + g("IndefiniteLivedIntangibleAssetsExcludingGoodwill")
    servicing = max(g(t) for t in INSTANT["servicing"])
    totals = [t for t in (g("IntangibleAssetsNetExcludingGoodwill"), g("OtherIntangibleAssetsNet")) if t]
    if not servicing or not totals:
        lumped = False
    elif parts:
        lumped = any(abs(t - parts - servicing) < abs(t - parts) for t in totals)
        totals = [t for t in totals if abs(t - parts - servicing) >= abs(t - parts)]
    else:
        lumped = None
    other = max(totals + [parts])
    both = g("IntangibleAssetsNetIncludingGoodwill")
    if lumped:
        both = min(both, g("Goodwill") + other) if both else 0.0
    goodwill = g("Goodwill") if tagged(["Goodwill", "IntangibleAssetsNetIncludingGoodwill"]) else None
    if both > (goodwill or 0) + other:
        goodwill = both - other
    # Other intangible assets count as untagged when only the tag for both is ("combined"), which some banks put on
    # goodwill alone while tagging their core deposit intangibles only in the annual report (CNB Financial's June 2026
    # $87.5M of "goodwill and other intangibles", beside $31.7M of core deposit intangibles), and others on both
    # (Eastern Bankshares' $1.28B); _tangible_equity tells them apart.
    separate = tagged(INSTANT["intangibles"][:-1])
    # Preferred stock at its carrying amount, or at its liquidation preference where that is all but nominal (First
    # Busey's June 2026: $223 of par value beside a $222.75M liquidation preference).
    preferred = max(g(t) for t in INSTANT["preferred_stock"][:-1])
    if preferred < 0.5 * g(INSTANT["preferred_stock"][-1]):
        preferred = g(INSTANT["preferred_stock"][-1])
    return {"equity": equity, "assets": assets, "goodwill": goodwill,
            "other": other if separate else None,
            "combined": not separate and both > 0 and both >= g("Goodwill"),
            "preferred": preferred if tagged(INSTANT["preferred_stock"]) else None,
            "servicing": servicing, "lumped": lumped, "shares": _val(facts.get(BANK_SHARES))}


def _balance_sheet(by_quarter, quarters, bank_failed=frozenset(), lease_failed=frozenset()):
    """Every balance sheet figure from the same quarter, so one stale line can't mix with newer ones."""
    q = next((q for q in quarters if any(t in by_quarter.get(q, {}) for t in ANCHORS)), None)
    if q is None:
        return {}
    facts = by_quarter[q]
    total, lt, reported = _debt({t: d["val"] for t, d in facts.items()})
    out = {m: _val(_first(facts, INSTANT[m])) for m in ("equity", "cash", "st_investments", "current_assets", "current_liabilities")}
    out.update(as_of=_first(facts, ANCHORS).get("end"), total_debt=total, lt_debt=lt, debt_reported=reported,
               total_assets=_val(facts.get("Assets")),
               # Long-term marketable securities, which derive() counts with cash, and minority holders' stakes in
               # subsidiaries (both lines added; a negative one, a deficit, counts as none).
               lt_securities=max(_val(_first(facts, INSTANT["lt_securities"])) or 0, 0),
               minority_interest=sum(max(_val(facts.get(t)) or 0, 0) for t in INSTANT["minority_interest"]),
               # For REITs: equity with minority holders' share (_ffo), total liabilities and the debt tags _reit_debt()
               # reads besides those _debt() does.
               equity_total=_val(facts.get(INSTANT["equity"][1])), liabilities=_val(facts.get("Liabilities")),
               # The equity above is that total, minority holders' stakes included, where the company tags no
               # StockholdersEquity of its own (derive's equity_includes_minority).
               equity_from_total=INSTANT["equity"][0] not in facts and INSTANT["equity"][1] in facts,
               reit_debt={t: facts[t]["val"] for t in INSTANT["reit_debt"] + ["SecuredDebt", "LineOfCredit"] if t in facts})
    # The most cash held at any recent quarter end, since interest earned last year came from the cash held then.
    out["peak_cash"] = max((_val(_first(f, INSTANT["cash"])) or 0) + (_val(_first(f, INSTANT["st_investments"])) or 0)
                           + max(_val(_first(f, INSTANT["lt_securities"])) or 0, 0) for f in by_quarter.values())
    # For the debt check (_debt_check): the most debt read at any recent quarter end, since interest paid last year
    # was on the debt held then, and noncurrent operating lease liabilities (None where the frame failed).
    # A quarter's reading above that quarter's total liabilities (or assets, or else the latest ones) is itself a
    # misread (a $10.7 trillion reading at RGC Resources, a $0.2B company), so it doesn't count.
    peak = [(_debt({t: d["val"] for t, d in f.items()})[0],
             _val(f.get(INSTANT["liabilities"][0])) or _val(f.get(INSTANT["total_assets"][0])))
            for f in by_quarter.values()]
    cap = _val(facts.get(INSTANT["liabilities"][0])) or _val(facts.get(INSTANT["total_assets"][0]))
    out["peak_debt"] = max([d for d, c in peak if not (c or cap) or d <= (c or cap)] or [0])
    out["lease_nc"] = None if q in lease_failed else _val(facts.get(INSTANT["lease_nc"][0])) or 0
    # For banks (_bank_parts): the lines behind tangible book value at each recent quarter end, by date, so that the
    # latest gives the price to tangible book value and returns are measured against the balance sheet at the end of the
    # year they were earned in (derive), not one changed since by a merger. None for a quarter a frame failed for.
    out["quarters"] = {}
    for p, f in by_quarter.items():
        anchor = _first(f, ANCHORS)
        if anchor:
            out["quarters"][anchor["end"]] = _bank_parts(f, p not in bank_failed)
    return out


def _debt(v):
    """(total debt, long-term part, whether any debt figure was reported) from one quarter's tag values."""
    if not any(t in v for t in DEBT):
        return 0, 0, False
    g = lambda tags: next((v[t] for t in tags if t in v), None)

    # Broad tags. DebtCurrent already includes current maturities and short-term borrowings, and
    # LongTermDebt includes its own current portion. An identical figure under two tags is one debt.
    cur_ltd = g(["LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"])
    st = g(["ShortTermBorrowings", "CommercialPaper"])
    if st == cur_ltd:
        st = None
    cur = g(["DebtCurrent"])
    if cur is None and (cur_ltd is not None or st is not None):
        cur = (cur_ltd or 0) + (st or 0)
    lt = g(["LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations", "LongTermNotesAndLoans"])
    # LongTermDebt also stands in when the noncurrent figure is far too small to be the same debt, which happens
    # when a company puts a single small loan or lease under that tag.
    if "LongTermDebt" in v and (lt is None or lt + (cur or 0) < 0.5 * v["LongTermDebt"]):
        lt = v["LongTermDebt"]
        if cur_ltd is not None and lt >= cur_ltd:
            lt -= cur_ltd
        elif lt == cur:
            lt = 0
    options = [((lt or 0) + (cur or 0), lt or 0)]

    # Instrument tags: one figure per kind of debt. Near-equal figures are the same debt tagged twice.
    kinds = []
    for whole, nc, c in DEBT_FAMILIES:
        w, n, cu = g(whole), g(nc), g(c)
        if w is not None or n is not None or cu is not None:
            amount = max(w or 0, (n or 0) + (cu or 0))
            kinds.append((amount, min(cu or 0, amount)))
    kept = []
    for amount, current in sorted(kinds, reverse=True):
        if not any(abs(amount - k[0]) <= 0.03 * k[0] for k in kept):
            kept.append((amount, current))
    # One figure matching all the others combined is their total (notes payable made up of senior notes and loans).
    if len(kept) > 2 and abs(sum(a for a, _ in kept[1:]) - kept[0][0]) <= 0.03 * kept[0][0]:
        kept = kept[:1]
    options.append((sum(a for a, _ in kept), sum(a - c for a, c in kept)))

    # Reported totals sometimes leave out short-term borrowings or leases, so they act as a floor.
    t = max((v[x] for x in DEBT_TOTALS if x in v), default=None)
    if t is not None:
        current = cur if cur is not None else sum(c for _, c in kept)
        options.append((t, max(t - current, 0)))
    total, long_term = max(options, key=lambda o: o[0])
    return total, long_term, True


FEDERAL_TAX = 0.21  # the US federal corporate income tax rate
# State income tax, net of the federal deduction for it. In the tax rate reconciliations of US companies (those
# starting from 21%), in the 3,746 profitable years from 2022 to 2025 that show their normal tax (_clean_rate) and
# have a state line, it came to a median 2.7% of pretax income, half of them between 1.3% and 4.0%. Their whole tax
# in such years came to a median 22.3%. On September 2026 data, rates from 22% to 24% predicted the tax rates of
# companies without a steady rate of their own about equally well (2025 from 2022 to 2024, and the quarters after
# their newest year), and 21% predicted them worse. Those companies went on to pay a median 1.5 to 2.3 points less
# than 23.5%, many of them still using up past losses, which this rate is meant to look past.
STATE_TAX = 0.025
# The rate a company's profit is normally taxed at when its own history can't show it (_normal_rate).
STATUTORY_TAX = FEDERAL_TAX + STATE_TAX
OWN_RATE = "own"  # derive()'s tax_rate for a company taxed at its own steady rate where it has one
# A year's tax rate shows what the company normally pays (_clean_rate) only when the tax rate reconciliation's
# one-time lines (valuation allowance, tax law changes, settlements, disposals, uncertain tax positions) come to at
# most CLEAN_TAX of pretax income, its valuation allowance moved by at most CLEAN_VA of it even if it does so every
# year (Broadwind's grew by 23% of its pretax income in 2023), and goodwill written down and gains or losses on sales
# come to at most CLEAN_ITEMS of it. Its own rate counts when at least OWN_YEARS such years agree within OWN_SPREAD,
# and it is at most OWN_MAX (_steady, _normal_rate).
# Filings for 2025, the first under the new income tax disclosure rules, tag the valuation allowance less often
# (42% of profitable company-years, against 55% to 58% in 2022 to 2024) and uncertain tax positions more often (38%,
# against 15% to 24%), so these shares are worth measuring again each year.
CLEAN_TAX = 0.02
CLEAN_VA = 0.10
CLEAN_ITEMS = 0.25
OWN_YEARS = 2
OWN_SPREAD = 0.08
OWN_MAX = 0.40
CASH_YIELD = 0.05  # interest a company can earn on its cash, which counts as ordinary income rather than a one-time gain
# How large a one-time item must be to count, against the company's earnings scale: reported net income, or 2% of
# revenue (SCALE_SALES) when that is larger, so a year near breakeven doesn't make every small item count. Each item
# must move net income by ITEM_MIN of that, all of them together by TOTAL_MIN, and an unusual tax bill must also be
# at least TAX_MIN of pretax income away from normal.
SCALE_SALES = 0.02
ITEM_MIN = 0.02
TOTAL_MIN = 0.05
TAX_MIN = 0.10
# Room above a company's best operating margin in its other years that a written-down year may reach once the
# write-downs are added back. More means the write-down was not in operating income (usually a business later
# reported as discontinued) or the figure is misread.
MARGIN_ROOM = 0.10

PROPERTY = ("gw_impairment", "impairment", "sale")  # items a REIT leaves out whole

# Kinds of one-time item in derive()'s "one_time" list. Each is {kind, amount, effect, ...}: `amount` is the item's
# size as reported (before tax, except for discontinued operations and tax items), and `effect` is the change made to
# net income (negative takes a gain out, positive adds a charge back). `rate` is the tax rate applied to the item,
# `reported` the whole amount as reported when only part of it counts as one-time, and `share` the owners' share of
# the item when minority holders own part of the business (tax items and discontinued operations never carry one: a
# tax item is the owners' own, and discontinued operations are the owners' share already).
KINDS = {
    "goodwill_impairment": "goodwill written down (added back before tax unless the filing shows a tax saving)",
    "impairment": "other assets written down: intangibles, property, leases, real estate",
    "sale_gain": "gain on selling a business or assets",
    "sale_loss": "loss on selling a business or assets",
    "securities_gain": "gain on investment securities, sold or revalued (Alphabet's 2025: $24.6B)",
    "securities_loss": "loss on investment securities, such as a bank selling bonds to reinvest at higher rates",
    "debt_gain": "gain on paying off debt for less than its book value",
    "debt_loss": "loss on paying off debt early",
    "gain": "other income below operating income beyond interest on cash and what came in the two prior years",
    "discontinued": "profit from businesses sold or shut down",
    "discontinued_loss": "loss from businesses sold or shut down",
    "tax_benefit": "tax bill below normal: `amount` is how far below, `reported_tax` the bill as reported, `normal_tax` "
                   "the normal bill on `adj_pretax` (pretax income without the other items), `rate` the normal rate "
                   "that starts from and `basis` where that comes from (own: the company's steady rate in years "
                   "without one-time tax items; statutory: STATUTORY_TAX), and `cause` is valuation_allowance when a "
                   "tagged valuation allowance release explains most of it",
    "tax_charge": "tax bill above normal, such as a valuation allowance being set up or tax assets remeasured (same "
                  "fields as tax_benefit)",
}


def _non_operating(s):
    """Income between operating income and pretax income, other than from equity-method investments and the tagged
    gains that _adjust() takes out on their own. Only gains, so a tagged loss inside operating income cannot make
    the rest look like a gain, and not a sale gain that discontinued operations already hold (i3 Verticals' 2025)."""
    pretax, op = s.get("pretax"), s.get("operating_income")
    if pretax is None or op is None:
        return None
    sale = max(s.get("sale") or 0, 0) - max(s.get("discontinued") or 0, 0) / (1 - FEDERAL_TAX)
    tagged = max(sale, 0) + max(s.get("securities") or 0, 0) + max(s.get("debt_extinguishment") or 0, 0)
    return pretax - op - ((s.get("equity_method") or 0) if s.get("pretax_has_equity_method") else 0) - tagged


def _reit_year(s):
    """A REIT's year as _adjust() reads it. Its gains on selling real estate count with its other sales, the larger
    reading winning, since the tags overlap (Realty Income's 2025: $177.6M, only on its own line). A gain on selling
    property and a loss on selling a stake in a venture (or the reverse) are two deals, both left out of funds from
    operations, so they add up where _sale() would keep only the stake (Highwoods Properties' 2025: a $107.1M gain and
    a $4.7M loss). The other gains funds from operations leaves out (REIT_GAIN) are added. A REIT that tags no income
    tax or pretax income (Kite Realty's 2025) pays none that matters, so its pretax income is what its net income adds
    up to, and the income statement is taken to reconcile."""
    s = dict(s)
    sale, assets = s.get("sale"), s.get("sale_assets")
    if sale and assets and sale * assets < 0:
        sale = sale + assets
    prop = s.get("property_sale")
    if prop and sale is not None:
        sale = prop if sale * prop < 0 or abs(prop) > abs(sale) else sale
    if sale is not None:
        s["sale"] = sale + (s.get("reit_gain") or 0)
    if s.get("net_income") is not None and (s.get("pretax") is None or s.get("income_tax") is None):
        tax = s.get("income_tax") or 0
        if s.get("pretax") is None:
            eq = s.get("equity_method") or 0
            s.update(pretax=s["net_income"] + tax - eq - (s.get("discontinued") or 0) + (s.get("minority") or 0),
                     pretax_has_equity_method=False)
        s["income_tax"] = tax
    return s


def _own_discontinued(s):
    """The owners' share of discontinued operations: the tagged figure when it is plausible, else the whole. Minority
    holders can take at most their own part (DNA X's 2024: $58.3M tagged as the owners' share of a $30.8M loss, with
    no minority holders)."""
    disc, own, mi = s.get("discontinued") or 0, s.get("discontinued_parent"), s.get("minority") or 0
    if own is None or own * disc < 0 or abs(own) > abs(disc) + abs(mi) + 1e5:
        return disc
    return own


def _minor(mi, s):
    """Whether a year's minority result is too small next to the business to be a share of it."""
    return abs(mi) < 0.1 * max(abs(s.get("net_income") or 0), SCALE_SALES * (s.get("revenue") or 0))


def _owners_share(s):
    """The owners' share of a year's continuing results, where minority holders own part of the business (NET Power's
    2025: 35%, the rest belonging to the holders of its operating company's units), or None when the owners and the
    minority holders went opposite ways. Above 1 when the minority holders lost money while the owners made it."""
    mi, ni = s.get("minority") or 0, s.get("net_income")
    if ni is None or _minor(mi, s):
        # A minority stake this small next to the business is a slice of a few ventures, not a share of the whole,
        # and dividing by a year's net income near breakeven would make it look large (Bloomin' Brands' 2025: $5.0M
        # to minority partners, against its $8.2M).
        return 1.0
    mine, whole = ni - _own_discontinued(s), ni + mi - (s.get("discontinued") or 0)
    return mine / whole if mine * whole > 0 else None


def _minority_whole(series, years):
    """Whether minority holders own a share of the whole business, as in an Up-C, rather than separate ventures. A
    share of the whole moves with the owners' result (Bumble's minority holders took 30% of each year's loss in 2022
    to 2025), while a stake in separate ventures brings in much the same every year whatever the owners make
    (Alexandria Real Estate's $149M to $213M in 2022 to 2025, against owners' results from a $1.4B loss to a $522M
    profit). With three years or more the likelier of the two counts; with fewer, only a minority result at least
    half the owners' own, the same way, is taken as a share of the whole (NET Power, public since 2023)."""
    pts = []
    for x in years:
        s = series[x]
        mi, ni = s.get("minority") or 0, s.get("net_income")
        if ni is not None and not _minor(mi, s):
            pts.append((ni - _own_discontinued(s), mi))
    if len(pts) >= 3:
        k = statistics.median(mi / mine for mine, mi in pts if mine)
        steady = statistics.median(mi for _, mi in pts)
        return sum(abs(mi - k * mine) for mine, mi in pts) < sum(abs(mi - steady) for _, mi in pts)
    return bool(pts) and all(mine * mi > 0 and abs(mi) >= 0.5 * abs(mine) for mine, mi in pts)


def _owners(series, has, reit=False):
    """{year: the owners' share of the year's results} for the years `has` (with net income). Where minority holders
    own a share of the whole business, an item moves net income (the owners' share) only by that much. A year far from
    the company's usual share, or one that can't be measured, takes the usual share. Where the minority holders own
    separate ventures instead, the items are unlikely to be in them, so they count whole (_minority_whole).

    In a REIT (`reit`), minority holders who took a large share of the result the same way in every year are also taken
    to own a share of the whole, even when their share drifts: that is how an operating partnership's unitholders show
    as units are swapped for shares (Strawberry Fields' Class A shareholders owned 11% of it in 2022 and 23% in 2025).
    Holders of single properties take much the same whatever the REIT makes, so a loss year sets them apart
    (Alexandria Real Estate's partners took $170M of profit in 2025, a year it lost $1.4B). Where a REIT tags no
    minority share and its net income is the whole business's (ni_whole, read from ProfitLoss), what its net income
    left to common shareholders implies stands in, less preferred dividends, when it gives much the same share of net
    income in at least two years (Empire State Realty's common shareholders took 58% to 60% of it in each of 2022 to
    2025, its operating partnership's other unitholders the rest), since an untagged preferred dividend would imply it
    in one year only (Sunstone Hotel's 2025). And a net income that exceeds the common shareholders' by just the
    minority share is the whole business's, whatever its tag (Clipper Realty's 2025 net loss of $52.3M, of which its
    Class A shareholders bore $19.9M and its operating partnership's other unitholders $32.4M; Simon Property's
    $5.36B), which also shows the minority holders own a share of the whole, so a year whose share can't be measured
    takes the usual one (Bluerock Homes' 2025, when preferred dividends turned its owners' small profit into a loss for
    its common shareholders)."""
    tracks = False  # whether the minority share is seen to be a share of the whole business
    if reit:
        series = list(series)
        implied = {x: series[x]["common_gap"] - (series[x].get("preferred") or 0) for x in has
                   if series[x].get("ni_whole") and series[x].get("minority") is None
                   and series[x].get("common_gap") is not None and series[x]["net_income"]}
        kept = [1 - mi / series[x]["net_income"] for x, mi in implied.items()]
        if len(kept) < 2 or not all(0.05 <= k <= 0.95 for k in kept) or max(kept) - min(kept) > 0.1:
            implied = {}
        for x in has:
            s = series[x]
            mi, gap = (implied[x], None) if x in implied else (s.get("minority"), s.get("common_gap"))
            # A net income whose gap to the common shareholders' is the minority share (and any preferred dividends)
            # still includes that share, whatever the tag.
            if x in implied or mi and gap is not None and abs(gap - (s.get("preferred") or 0) - mi) <= 0.02 * abs(mi) + 1e5:
                series[x] = dict(s, net_income=s["net_income"] - mi, minority=mi)
                tracks = tracks or not _minor(mi, s)
    shares = {x: _owners_share(series[x]) for x in has}
    usual_share = statistics.median([v for v in shares.values() if v is not None and v != 1.0] or [1.0])
    whole = tracks or _minority_whole(series, has) or reit and _minority_alike(series, has)

    def owners(x):
        if not whole or usual_share > 0.9:
            return 1.0
        v = shares.get(x)
        return min(1.0, v if v is not None and abs(v - usual_share) <= 0.25 else usual_share)

    return {x: owners(x) for x in has}


def _minority_alike(series, years):
    """Whether minority holders took a result the same way as the owners, and at least half as large, in every year
    with net income (at least two). _minority_whole() applies this test when it has fewer than three years."""
    pts = [(s["net_income"] - _own_discontinued(s), s.get("minority") or 0)
           for s in (series[x] for x in years) if s.get("net_income") is not None]
    return len(pts) >= 2 and all(mine * mi > 0 and abs(mi) >= 0.5 * abs(mine) for mine, mi in pts)


def _reconciles(s):
    """Whether the income statement lines add up to reported net income. Otherwise one of them is misread and any
    adjustment built on them would be too."""
    ni, pretax, tax = s.get("net_income"), s.get("pretax"), s.get("income_tax")
    if ni is None or pretax is None or tax is None:
        return False
    eq = 0 if s.get("pretax_has_equity_method") else s.get("equity_method") or 0
    return abs(pretax - tax + eq + (s.get("discontinued") or 0) - (s.get("minority") or 0) - ni) \
        <= 0.1 * max(abs(ni), abs(pretax)) + 1e5


def _scaled(values, j, series):
    """The other years' amounts, each scaled to year j's sales (within half to double)."""
    rev = series[j].get("revenue")
    out = []
    for x, v in values.items():
        if x == j or v is None:
            continue
        rx = series[x].get("revenue")
        out.append(v * (min(2.0, max(0.5, rev / rx)) if rev and rx and rev > 0 and rx > 0 else 1.0))
    return out


def _one_off(amount, others, level=False):
    """The one-time part of a gain (positive) or charge (negative), given the same item in the company's other
    years. Something that comes in most years is part of the business, so the usual size is what this year and
    enough other years to make a majority reach in the same direction: the second largest of three other years, the
    largest of one or two.

    An item is wholly one-time at four times its usual size or more (Hewlett Packard Enterprise's $1.58B goodwill
    write-down against $0.9B, nothing and $0.1B in the three years before) and part of the business at twice or less
    (Cracker Barrel's $19.8M of store write-downs against $17.4M and $11.7M), with the share in between sliding from
    all to none. A tax bill is a `level`: whatever lies beyond the usual gap from normal counts, fading in as the
    usual gap grows from a quarter to half of this year's.
    """
    if not amount:
        return 0.0
    same = sorted((abs(v) for v in others if v * amount > 0), reverse=True)
    k = (len(others) + 1) // 2
    usual = same[k - 1] if k and len(same) >= k else 0.0
    ratio = usual / abs(amount)
    if level:
        return math.copysign(max(0.0, abs(amount) - usual * min(1.0, max(0.0, (ratio - 0.25) / 0.25))), amount)
    return amount * min(1.0, max(0.0, (0.5 - ratio) / 0.25))


def _tax_one_off(gap, others, opposite=True):
    """The one-time part of a tax gap (positive: tax above normal), given the gaps the company's other years show.
    What most of the window reaches in the same direction is usual (this year counting toward the majority, as in
    _one_off) and never counts, fading in as it grows from a quarter to half of this year's gap, as in _one_off. A gap
    every other year shows the other way is usual too, wholly, so this year's is measured from the smallest of them
    (Qualcomm's 2025 tax charge against a rate below the statutory rate in each of 2022 to 2024, from its low-taxed
    foreign-derived income)."""
    if not gap:
        return 0.0
    same = sorted((abs(v) for v in others if v * gap > 0), reverse=True)
    k = (len(others) + 1) // 2
    if k and len(same) >= k:
        return _one_off(gap, others, level=True)
    against = sorted((abs(v) for v in others if v * gap < 0), reverse=True)
    if opposite and len(against) == len(others) >= 2:
        return gap + math.copysign(against[-1], gap)
    return gap


def _growing(values, j, series):
    """Whether an item came the same way in each of the two years before, last year at least half this year's size,
    which makes a growing gain or loss on sales part of the business even while the oldest year was much smaller
    (Portland General Electric's losses on selling other assets: $24M, $112M and $179M in 2023 to 2025, which are its
    sales of tax credits). Not write-downs: two large ones in a row mark a business in decline, and each is one-time
    (Kraft Heinz's brand write-downs: $152M, $2.0B and $2.6B in 2023 to 2025)."""
    now, last, before = values.get(j), values.get(j - 1), values.get(j - 2)
    if not now or not last or not before or now * last <= 0 or now * before <= 0:
        return False
    return abs(_scaled({j - 1: last}, j, series)[0]) >= 0.5 * abs(now)


def _tax_one_time(series, years):
    """({year: valuation allowance movement that is one-time}, {year: the tax bill that one-time lines of the tax rate
    reconciliation explain, positive where they raised it}, {year: the size of those lines, whichever way each went}).

    A line that comes the same way at a similar size most years is part of the company's tax, so only the part well
    beyond its usual size counts (_one_off): a valuation allowance that grows every year (losses in one country that
    can't offset profits in another, as at ManpowerGroup and HubSpot), or a lower rate a country grants year after year
    that the filings show as a tax law line (Check Point's Israeli rate, 8% to 18% of pretax income below the
    statutory rate in each of 2022 to 2025, or Tower Semiconductor's). A valuation allowance released always counts,
    since that ends once the old losses are used."""
    allowance = {x: series[x].get("valuation_allowance") for x in years}  # None: not reported that year
    va_one = {x: (_one_off(v, _scaled(allowance, x, series)) if v > 0 else v) if v is not None else 0
              for x, v in allowance.items()}
    signed, size = dict(va_one), {x: abs(v) for x, v in va_one.items()}
    for k in TAX_LINES:
        line = {x: (series[x].get("tax_lines") or {}).get(k) for x in years}
        for x, v in line.items():
            if v:
                one = _one_off(v, _scaled(line, x, series))
                signed[x] += one
                size[x] += abs(one)
    return va_one, signed, size


def _clean_rate(s, one_time=None):
    """A year's tax as a share of pretax income, when that shows what the company normally pays, else None. That
    takes a profit, read as the company reports it (its effective tax rate, where it tags one, or else an income
    statement that adds up), a tax rate reconciliation without one-time lines beyond CLEAN_TAX of pretax income (a
    valuation allowance set up or released, deferred taxes remeasured for a change in tax law, a settlement with the
    tax authorities, tax on a disposal, a reserve for an uncertain tax position set up or released) and without a
    valuation allowance movement beyond CLEAN_VA of it even when it recurs, and goodwill write-downs and gains or losses
    on sales within CLEAN_ITEMS of it (a goodwill write-down is rarely deductible, and what a sale is taxed on depends
    on what the company paid for the asset). Other write-downs, gains on investments and on paying off debt are taxed
    like the rest of its income, so they don't change the rate (an insurer's investment gains and losses come every
    year: Progressive's 20.4% to 21.7% in 2022 to 2025).
    A year that used up losses carried forward shows a valuation allowance released: EverQuote's 5.4% in 2024 came
    with a 16.0% release, and its tax ran at 22.5% in the first half of 2026, once the allowance was gone.
    `one_time` is the size of the year's one-time lines (_tax_one_time), which leaves out lines that come at a similar
    size most years; by default, all of them."""
    p, t, lines = s.get("pretax"), s.get("income_tax"), s.get("tax_lines")
    if not p or p <= 0 or t is None or lines is None:
        return None
    etr = s.get("etr")
    if etr is None and not _reconciles(s):
        return None
    # Some companies state the rate on their own share of the profit, without minority holders' share, on which they
    # pay no tax (U.S. Physical Therapy's 33.4% in 2025, which is 25.5% of all its pretax income).
    if etr is not None and all(abs(etr - t / b) > 0.01 for b in (p, p - (s.get("minority") or 0)) if b > 0):
        return None
    items = [s.get(k) for k in ("gw_impairment", "sale")]
    if None in items or sum(abs(v) for v in items) > CLEAN_ITEMS * p:
        return None
    va = s.get("valuation_allowance") or 0
    if one_time is None:
        one_time = abs(va) + sum(abs(v) for v in lines.values())
    if abs(va) > CLEAN_VA * p or one_time > CLEAN_TAX * p:
        return None
    return t / p


def _steady(clean, series):
    """The median of the years' rates ({year: rate}) when at least OWN_YEARS of them agree within OWN_SPREAD, all of
    them or all but one year whose profit was small, which makes fixed amounts weigh more (Qiagen's 31.0% on $121M of
    pretax income in 2024, against $430M to $513M in its other years); not a year of normal profit (Jackson
    Financial's 19.5% on $7.7B in 2022, against 0.4% and 4.5% on about $1B in 2023 and 2024). Else None."""
    clean = dict(clean)
    for dropped in (False, True):
        rates = sorted(clean.values())
        if len(rates) < OWN_YEARS:
            return None
        mid = statistics.median(rates)
        if rates[-1] - rates[0] <= OWN_SPREAD:
            return mid
        if dropped or len(rates) < OWN_YEARS + 1:
            return None
        off = max(clean, key=lambda x: abs(clean[x] - mid))
        if series[off]["pretax"] >= 0.5 * statistics.median(series[x]["pretax"] for x in clean):
            return None
        del clean[off]
    return None


def _normal_rate(series, j, years, tax_rate, one_time=None):
    """(rate, basis): the tax rate year j's profit is normally taxed at, and where it comes from.

    A REIT's is 0 ("reit"), since it pays no corporate income tax on the profit it pays out. Otherwise it is the
    company's own rate ("own") where its years that show it (_clean_rate, year j included) agree: the median of them,
    when at least OWN_YEARS do, all within OWN_SPREAD of each other or all but one when there are more (Qiagen's 17.4%,
    20.6% and 13.3% in 2022, 2023 and 2025, besides 31.0% on a small 2024 profit), and the median is at most OWN_MAX.
    That keeps a rate that is low year after year for a lasting reason, such as profits earned abroad (Applied
    Materials: 12%) or an exemption (Carnival's cruises: under 1% in 2024 and 2025). Else it is the 21% federal rate
    plus a typical state income tax ("statutory"), which is also a utility's (whose tax regulators and energy credits
    shape, so _adjust() treats any tax benefit on its profit as normal). Losses in some years, and years whose low tax
    came from using up losses carried forward, say nothing about the rate once those losses are used up (EverQuote's
    2024: 5.4%). Tax credits the company earns every year are allowed for by _adjust()."""
    if tax_rate == 0:
        return 0.0, "reit"
    if tax_rate is None:
        return STATUTORY_TAX, "statutory"
    if one_time is None:
        one_time = _tax_one_time(series, [x for x in years if series[x].get("net_income") is not None])[2]
    clean = {x: r for x in years if (r := _clean_rate(series[x], one_time.get(x))) is not None}
    # Year j counts too when it agrees with the others, and otherwise is judged against them.
    for pool in (clean, {x: r for x, r in clean.items() if x != j}):
        rate = _steady(pool, series)
        if rate is not None:
            return (rate, "own") if 0 <= rate <= OWN_MAX else (STATUTORY_TAX, "statutory")
    return STATUTORY_TAX, "statutory"


def _tax_gap(pretax, tax, rate):
    """How far a tax bill is from normal: positive when above it. On a pretax loss anything from no tax at all to a
    full credit at the normal rate is normal (Ford's 31% credit on its 2025 loss), since a company with past losses
    may not record one."""
    if pretax > 0:
        return tax - rate * pretax
    return tax - min(max(tax, rate * pretax), 0.0)


def _adjust(series, years, cash, tax_rate, abroad=False, portfolio=False):
    """{year index: (net income without one-time items, [items])} for each of `years` (indexes into `series`, the
    window the company is measured over). With `portfolio` (derive's lenders whose investments are the business),
    gains and losses on investment securities are never one-time.

    Adds back one-time charges and takes out one-time gains: write-downs, gains and losses on selling a business,
    assets or investment securities and on paying off debt (all from their XBRL tags), untagged income below
    operating income beyond interest on cash, discontinued operations, and an unusual tax bill. An item is one-time
    only when it is well above what the company reports of the same kind in most of its other years (_one_off), or
    growing year after year (_growing), so a restaurant's yearly store write-downs or an aircraft lessor's gains on
    selling planes stay in; a REIT's property gains and write-downs always come out, as funds from operations does.
    Add-backs and take-outs are after tax at the company's normal rate (_normal_rate), or at none when it is recording
    no tax on its results, and a goodwill write-down is added back whole unless the filing shows a tax saving. Where
    minority holders own part of the business, only the owners' share of each item moves net income (_owners_share).

    The tax bill on what is left is then compared with normal (_tax_gap), and only the one-time part of any gap comes
    out (_tax_one_off): the part beyond the company's usual gap both as a share of pretax income and in dollars (so
    structurally low rates and recurring credits stand), plus a usual gap every other year shows the other way, less
    the tax credits it reports every year, and for a tax bill above normal also the part beyond the year before. What
    one-time lines of the tax rate reconciliation explain (_tax_one_time) is never usual, lines that come at a similar
    size most years are, and a normal tax on a profit is no lower than the company paid in its other profitable years.
    In a year whose reconciliation shows no one-time lines (_clean_rate), a move from the company's own rate toward the
    statutory rate counts only beyond it, and without a steady rate of its own a low tax bill counts only against the
    federal rate alone, and not at all at an ordinary rate for a company based abroad (`abroad`). A `tax_rate` of None
    (a utility) makes any tax benefit on a profit normal. Nothing changes unless the income statement lines add up to
    reported net income.
    """
    reit = tax_rate == 0
    if reit:
        series = [_reit_year(s) for s in series]
    ok = [j for j in years if _reconciles(series[j])]
    has = [x for x in years if series[x].get("net_income") is not None]
    vals = {}
    for k, sign in (("gw_impairment", -1), ("impairment", -1), ("sale", 1), ("securities", 1), ("debt_extinguishment", 1)):
        if k == "securities" and portfolio:
            continue  # its gains and losses on its investments stay in its results (derive)
        vals[k] = {x: None if series[x].get(k) is None else sign * series[x][k] for x in has}
    for x in has:
        # Goodwill written down this year and last, tagged together as another write-down (Brunswick's 2025: $385.8M
        # of indefinite-lived intangibles, which is its $305.8M and $80.0M goodwill write-downs of 2025 and 2024).
        gw, before = series[x].get("gw_impairment") or 0, (series[x - 1].get("gw_impairment") or 0) if x else 0
        if gw and before and abs(-(vals["impairment"][x] or 0) - gw - before) <= 0.01 * (gw + before):
            vals["impairment"][x] = 0.0
    share_of = _owners(series, has, reit)
    va_one, tax_one, tax_size = _tax_one_time(series, has)
    rates, bases, pre, owned = {}, {}, {}, {}
    for j in ok:
        s = series[j]
        r, bases[j] = _normal_rate(series, j, years, tax_rate, tax_size)
        rates[j] = r
        own = owned[j] = share_of[j]
        # The tax an item actually carried. A company that recorded no tax benefit on a loss, or paid next to no tax
        # on a profit while releasing its valuation allowance, has losses it cannot yet use, so a write-down saved it
        # no tax and a gain cost it none (Comtech's 2025 write-downs: a $0.1M benefit on a $155M loss). A loss that
        # only a goodwill write-down made, which is not deductible anyway, doesn't count.
        va = s.get("valuation_allowance") or 0
        if s["pretax"] + (s.get("gw_impairment") or 0) <= 0 and s["income_tax"] >= 0.1 * r * s["pretax"] or (
                s["pretax"] > 0 and abs(s["income_tax"]) <= 0.05 * s["pretax"] and va <= -0.5 * FEDERAL_TAX * s["pretax"]):
            m = 0.0
        else:
            m = r
        ni, rev = s["net_income"], s.get("revenue") or 0
        scale = max(abs(ni), SCALE_SALES * rev)
        big = lambda effect: effect != 0 and abs(effect) >= ITEM_MIN * scale
        mine = lambda item: dict(item, share=round(own, 3)) if own < 0.995 else item
        # A REIT's gains on selling property and its write-downs are left out whole, however often they come, as
        # the industry's funds from operations measure does (Realty Income writes property down every year).
        found = {k: v[j] if reit and k in PROPERTY
                 else 0.0 if k not in ("gw_impairment", "impairment") and _growing(v, j, series)
                 else _one_off(v[j], _scaled(v, j, series))
                 for k, v in vals.items() if v.get(j) is not None}

        # A write-down or sale inside discontinued operations is also in the cash flow statement tags, and the
        # discontinued result already comes out whole (Fidelity National Information Services' 2022: a $17.6B
        # goodwill write-down, all in its $17.3B discontinued loss; Mammoth Energy's 2025: $63M of write-downs,
        # about half of them in businesses it sold at a gain). So write-downs and sales shrink by the discontinued
        # result, the charges and the gains each by the whole of it. The discontinued result is after tax, so it is
        # grossed up at the normal rate; a remainder under a tenth of the item is that estimate's error, not a part
        # of it outside discontinued operations (Greif's 2025: a $1.09B gain on selling its containerboard business,
        # all in its $825M discontinued result).
        disc = s.get("discontinued") or 0
        for sign in (-1, 1) if big(disc) else ():
            room = abs(disc) / (1 - FEDERAL_TAX)
            for k in ("gw_impairment", "impairment", "sale"):
                if room > 0 and (found.get(k) or 0) * sign > 0:
                    cut = min(abs(found[k]), room)
                    found[k] -= sign * cut
                    room -= cut
                    if abs(found[k]) < 0.1 * abs(vals[k][j]):
                        found[k] = 0.0
        # Write-downs sit in operating income, so adding them back must leave a plausible operating margin.
        op = s.get("operating_income")
        margins = [series[x]["operating_income"] / series[x]["revenue"] for x in years
                   if x != j and series[x].get("operating_income") is not None and (series[x].get("revenue") or 0) > 0]
        down = -(found.get("gw_impairment", 0) + found.get("impairment", 0))
        if down > 0 and op is not None and rev > 0 and len(margins) >= 2:
            fit = max(0.0, (max(margins) + MARGIN_ROOM) * rev - op)
            for k in ("impairment", "gw_impairment"):
                cut = min(-found.get(k, 0), down - fit)
                if cut > 0:
                    found[k] += cut
                    down -= cut

        items, moved, taxes = [], 0.0, 0.0
        for k, kind in (("gw_impairment", "goodwill_impairment"), ("impairment", "impairment"), ("sale", "sale"),
                        ("securities", "securities"), ("debt_extinguishment", "debt")):
            x = found.get(k) or 0
            rate = min(s.get("gw_tax_rate") or 0, m) if k == "gw_impairment" else m
            if not big(x * (1 - rate) * own):
                continue
            if kind in ("sale", "securities", "debt"):
                kind += "_gain" if x > 0 else "_loss"
            items.append(mine({"kind": kind, "amount": abs(x), "effect": -x * (1 - rate) * own, "rate": round(rate, 4)}))
            if abs(x) < 0.995 * abs(vals[k][j]):
                items[-1]["reported"] = abs(vals[k][j])  # only part of it counts as one-time
            moved += x
            taxes += x * rate * own
        if op is not None and s["pretax"] > 0:
            # Income that came in every recent year is ordinary, such as Robert Half's deferred pay trusts (offset by
            # pay expense inside operating income) or interest on long-term investments.
            prior = [_non_operating(p) for p in series[max(0, j - 2): j]]
            usual = min(prior) if len(prior) == 2 and None not in prior else 0
            gain = _non_operating(s) - max(CASH_YIELD * max(cash or 0, 0), usual)
            if gain >= 0.25 * s["pretax"] and big(gain * (1 - m) * own):
                items.append(mine({"kind": "gain", "amount": gain, "effect": -gain * (1 - m) * own, "rate": round(m, 4)}))
                moved += gain
                taxes += gain * m * own
        d = _own_discontinued(s)  # net income is the owners' share, so only their share comes out of it
        if big(d):
            items.append({"kind": "discontinued" if d > 0 else "discontinued_loss", "amount": abs(d), "effect": -d})
        pre[j] = (s["pretax"] - moved, s["income_tax"] - taxes, items)

    gaps = {j: _tax_gap(p, t, rates[j]) for j, (p, t, _) in pre.items()}
    # What a year's gap says about the company's usual tax: the part one-time lines explain is not usual. A valuation
    # allowance released comes from losses carried forward (Power Solutions' tax was near zero in 2022 to 2024 as it
    # used old losses, then it released its allowance in 2025; its normal rate is not near zero).
    steady = {}
    for j, g in gaps.items():
        v = tax_one.get(j, 0)
        steady[j] = g - (max(v, g) if g < 0 else min(v, g)) if v * g > 0 else g
    # Each year's rate as the company normally pays it, for years whose pretax income was mostly not one-time items,
    # since the tax assumed on those swamps the rest (Uber's 2023: $1.8B of investment gains in $2.3B).
    usual_rate = {x: rates[x] + steady[x] / p for x, (p, _, _) in pre.items()
                  if p > 0 and abs(p - series[x]["pretax"]) <= 0.5 * abs(series[x]["pretax"])}
    # Without the item frames, write-downs stay in, and the tax a non-deductible goodwill write-down didn't save would
    # look like a one-time tax charge, so none is added back.
    degraded = any(series[x].get("impairment") is None for x in has)
    out = {}
    for j in years:
        s = series[j]
        if j not in pre:
            out[j] = (s.get("net_income"), [])
            continue
        pretax, tax, items = pre[j]
        ni, rev, r = s["net_income"], s.get("revenue") or 0, rates[j]
        scale = max(abs(ni), SCALE_SALES * rev)
        gap = gaps[j]
        if tax_rate is None and pretax > 0 > gap:
            gap = 0.0
        # The usual gap two ways, from this year's normal rate: in dollars scaled to sales (a restaurant's tip credits,
        # which stay put when profit falls) and as a share of pretax income (a low foreign rate). Only what is unusual
        # both ways counts. A usual gap the other way only counts against the statutory rate, since a company's own
        # steady rate is its usual tax already.
        others = {x: steady[x] + (rates[x] - r) * max(pre[x][0], 0) for x in pre if x != j}
        views = [_scaled(others, j, series)]
        if pretax > 0:
            views.append([(usual_rate[x] - r) * pretax if x in usual_rate else 0.0 for x in others])
        own_rate = bases[j] == "own"
        one_off = min((_tax_one_off(gap, v, opposite=not own_rate) for v in views), key=abs)
        va1, lines = va_one.get(j, 0), tax_one.get(j, 0)
        if one_off > 0 and others.get(j - 1, 0) > 0 and not lines >= 0.5 * gap:
            # A tax bill above normal two years running is more likely the company's tax structure (foreign profits
            # taxed while home losses are not) than a one-time charge, so the year before alone can make it usual,
            # unless this year's one-time lines explain it, such as a valuation allowance set up (Intel's 2025, after
            # an untagged one in 2024).
            # Not for benefits: Uber released valuation allowances in both 2024 and 2025, each one-time.
            one_off = min(one_off, _tax_one_off(gap, _scaled({j - 1: others[j - 1]}, j, series)))
        credits, usual_credits = s.get("tax_credits") or 0, 0.0
        if one_off < 0 < pretax and credits and not own_rate:
            # Tax credits the company earns every year are normal too (Good Times' restaurant tip credits, $1.2M in
            # 2025 against a tax bill $0.9M below the statutory rate), up to what it reported in its other years, in
            # dollars or as a share of pretax income, or up to the whole normal tax when it reports them for the
            # first time.
            # Not with its own steady rate, which already has them, nor on a loss, which leaves them nothing to
            # offset (Lyft's 2025: $344M of research credits, first reported the year its valuation allowance came off).
            before = {x: series[x].get("tax_credits") for x in has}
            if any(v for x, v in before.items() if x != j):
                usual_credits = credits - _one_off(credits, _scaled(before, j, series))
            else:
                usual_credits = min(credits, r * pretax)
            one_off = max(one_off, min(0.0, gap + usual_credits))
        if one_off < 0 < pretax and owned[j] < 0.95:
            # Minority holders of a partnership the company controls (an Up-C, or physicians' stakes in hospitals)
            # pay the tax on their share themselves, so a bill below normal by up to the normal rate on their share
            # is normal (TPG's 2025: $67M on $667M of pretax income, a third of it its own).
            one_off = min(0.0, one_off + r * pretax * (1 - owned[j]))
        normal = tax - one_off
        if one_off < 0 < pretax and tax_rate is not None:
            # Normal tax on a profit is no lower than the company paid in its other profitable years, up to the normal
            # rate, however low its usual tax in dollars would put it (Hims & Hers' 2025: at least the 14.9% of 2024,
            # once the valuation allowance it released that year is left out), nor below nothing. Unless it paid less
            # than nothing on a profit in some other year, which is how a company whose tax credits are fixed in
            # dollars shows (Cracker Barrel's tip credits: -69% in 2024).
            lows = [usual_rate[x] for x in usual_rate if x != j]
            if min(lows, default=0.0) >= 0:
                normal = max(normal, min(min(lows, default=0.0), r) * pretax)
        one_off = tax - normal
        if degraded and one_off > 0:
            one_off = 0.0
        floor = ITEM_MIN * scale
        clean = _clean_rate(s, tax_size.get(j)) is not None
        if one_off and pretax > 0 and clean and bases[j] == "statutory" and (
                0 <= tax <= OWN_MAX * pretax if abroad else one_off < 0 and tax > (FEDERAL_TAX - TAX_MIN) * pretax):
            # A year whose tax rate reconciliation shows no one-time lines, at a company with no steady rate of its own
            # to compare it with, is measured against the statutory rate only where that surely applies. State tax
            # depends on where a company earns its profit, so a low tax bill there has to be low against the federal
            # rate alone (Micron's 11.6% in 2025, with next to no state tax, and 13.7% to 15.0% in the quarters since;
            # SanDisk's 12.2% in 2026). And a company based abroad pays its own country's rate, which may be anything
            # from nothing (Bermuda) to 30% or more (Japan), so an ordinary rate there is its normal one.
            one_off = 0.0
        if one_off and abs(one_off) >= TAX_MIN * abs(pretax) and abs(one_off) >= floor:
            # A tax bill above normal right where charges were added back after tax most likely means those charges
            # saved no tax (Centene's 2025 write-down of goodwill it tagged as other assets), so that part moves into
            # the charges and the tax item shows only what they don't explain.
            saved = [n for n, i in enumerate(items) if i.get("rate") and i["effect"] > 0 < one_off]
            held = sum(items[n]["amount"] * items[n]["rate"] * items[n].get("share", 1) for n in saved)
            if held > 0:
                part = min(one_off, held) / held
                items = [dict(i, rate=round(i["rate"] * (1 - part), 4),
                              effect=i["amount"] * (1 - i["rate"] * (1 - part)) * i.get("share", 1))
                         if n in saved else i for n, i in enumerate(items)]
                one_off -= min(one_off, held)
            if clean and own_rate and pretax > 0 and one_off and (one_off > 0) == (r < STATUTORY_TAX):
                # A tax bill that moved from the company's own rate toward the statutory rate, in a year whose tax rate
                # reconciliation shows no one-time lines, is as likely a lasting change (credits that ended, profits
                # shifting between countries) as a one-time item, so only a move beyond the statutory rate counts
                # (TaskUs's 25.2% in 2025, against 37% to 39% before and 29.6% in the first half of 2026; Digi
                # International's 18.3%, against next to nothing before and 22.6% in the three quarters since). Whether
                # the tax bill was unusual enough to count at all was judged on the whole move, above.
                beyond = gap - (STATUTORY_TAX - r) * pretax
                kept = min(one_off, max(beyond, 0.0)) if one_off > 0 else max(one_off, min(beyond, 0.0))
                normal += one_off - kept
                one_off = kept
            # Tax on a loss year or a year near breakeven (foreign profits taxed while home losses are not, costs that
            # are never deductible), or any tax a REIT pays (on its taxable subsidiaries and abroad), usually recurs,
            # so a tax bill above normal there counts only when one-time lines explain it, such as a valuation
            # allowance being set up (Certara's 2025: $9.2M of tax on $7.6M of pretax income, 2% of its sales).
            if one_off > 0 and (pretax <= SCALE_SALES * rev or reit) and not lines >= 0.5 * one_off:
                one_off = 0.0
            if abs(one_off) >= floor:
                item = {"kind": "tax_charge" if one_off > 0 else "tax_benefit", "amount": abs(one_off), "effect": one_off,
                        "rate": round(r, 4), "basis": bases[j], "reported_tax": s["income_tax"], "normal_tax": normal,
                        "adj_pretax": pretax}
                if va1 * one_off > 0 and abs(va1) >= 0.5 * abs(one_off):
                    item["cause"] = "valuation_allowance"
                items = items + [item]
        total = sum(i["effect"] for i in items)
        if not items or abs(total) < TOTAL_MIN * scale:
            out[j] = (ni, [])  # too small to change any score
        else:
            out[j] = (ni + total, items)
    return out


def _ffo(series, years, adjusted):
    """{year index: (funds from operations, the same without one-time items, its parts, the owners' share)} for a
    REIT, with Nones where a year's figures can't give it.

    Funds from operations (FFO) is the earnings measure REITs report and are valued on. Nareit, the REIT industry
    body, defines it as net income to common shareholders plus the depreciation and amortization of real estate, less
    gains on selling property, plus write-downs of property. Depreciation spreads a building's cost over its life
    although well-kept property tends to hold its value, so net income understates what a REIT earns. Where minority
    holders own a share of the whole business, as the holders of an operating partnership's units do in most REITs,
    only the owners' share of depreciation, gains and write-downs counts (_owners): Strawberry Fields' Class A
    shareholders own about a fifth of it. Partners in single ventures who took a loss took at least that much of the
    ventures' depreciation, which comes out too (`partners`).

    Read from the filings' tags, it is not quite Nareit's figure. It adds back all the depreciation and amortization a
    REIT tags, where Nareit counts only real estate's, so it runs above the Nareit FFO of REITs with much equipment
    (Iron Mountain's 2025: $1.17B against $584M; Equinix's $3.47B against $2.67B), nearer the adjusted FFO they also
    report. And it leaves out the REIT's share of joint ventures' depreciation (Brandywine's $42M in 2025), partners'
    shares beyond their losses, and property gains tagged under a company's own names (Welltower's $1.4B in 2025).

    Without one-time items it is the net income left after _adjust() (`adjusted`, which takes out a REIT's property
    gains and write-downs whole, and other one-time items as for any company), less what goes to preferred and minority
    holders (_common_gap), plus the owners' share of depreciation. That is close to the "core" or "normalized" FFO many
    REITs also report, which leaves out such items as debt payoff costs. A year _adjust() left unchanged (items too
    small to count, or an income statement that doesn't add up) takes plain FFO, without discontinued operations
    (Sun Communities' 2025: a $1.43B result, mostly the gain on selling its marinas). Where the statement doesn't add
    up, the discontinued figure tagged and the one its continuing income implies can disagree, and the one that leaves
    FFO nearer the year's operating cash flow counts: Sun Communities' tagged $1.43B leaves $811M against $808M of cash
    flow from continuing operations, while Crown Castle's tagged $916M (its fiber business's results before a $1.58B
    loss on selling it) would leave $229M against $3.06B, and the -$659M its continuing income implies leaves $1.80B.
    Without a cash flow figure to settle it, the year's FFO without one-time items is unknown."""
    has = [x for x in years if series[x].get("net_income") is not None]
    share = _owners(series, has, reit=True)
    out = {}
    for j in years:
        s = _reit_year(series[j])
        ni, dep, gap = s.get("net_income"), s.get("depreciation"), s.get("common_gap")
        if ni is None or dep is None or gap is None:
            out[j] = (None, None, None, 1.0)
            continue
        own = share.get(j, 1.0)
        dep, gains, down = own * dep, own * (s.get("sale") or 0), own * max(s.get("impairment") or 0, 0)
        # Partners in single ventures (not a share of the whole business) who took a loss took at least that much of
        # the ventures' depreciation and write-downs, which funds from operations adds back only for the REIT's own
        # share: Acadia Realty's 2025 partners lost $51.3M, and its reported FFO is $46M below what counting all of it
        # would give.
        mi = s.get("minority") or 0
        partners = min(-mi, max(dep + down - gains, 0)) if mi < 0 and own >= 0.995 else 0.0
        ffo = ni - gap + dep - gains + down - partners
        adj_ni, items = adjusted[j]
        disc = 0.0
        if items and adj_ni is not None:
            adj = adj_ni - gap + dep - partners
        else:
            disc = _discontinued_for_ffo(s, ffo)
            if disc is None:
                adj = None
            else:
                disc = disc if abs(disc) >= ITEM_MIN * max(abs(ni), SCALE_SALES * (s.get("revenue") or 0)) else 0.0
                adj = ffo - disc
        out[j] = (ffo, adj, {"net_income_common": ni - gap, "depreciation": dep, "property_gains": gains,
                             "write_downs": down, **({"share": round(own, 3)} if own < 0.995 else {}),
                             **({"partners": partners} if partners else {}),
                             **({"discontinued": disc} if disc else {})}, own)
    return out


def _discontinued_for_ffo(s, ffo):
    """The owners' discontinued result a REIT's year (_reit_year) takes out of FFO when _adjust() left the year
    unchanged, or None when it can't be told (_ffo)."""
    tagged = _own_discontinued(s)
    if not tagged or _reconciles(s):
        return tagged
    eq = 0 if s.get("pretax_has_equity_method") else s.get("equity_method") or 0
    implied = s["net_income"] - (s["pretax"] - s["income_tax"] + eq - (s.get("minority") or 0))
    ocf = s.get("ocf")
    if not ocf or ocf <= 0:
        return None
    return min((tagged, implied), key=lambda d: abs(ffo - d - ocf))


# A working capital outflow in one of the cash-flow model's three years counts as a one-off payment (_one_off_cash) when
# it is above WC_SALES of that year's sales, above WC_TIMES the company's usual swing and larger than any outflow it had
# before. The usual swing is the median size of its changes in the years before the model's three (at least WC_YEARS of
# them, reaching back WC_HISTORY years before the other figures' window), so neither the year itself nor a second
# one-off year beside it counts as usual. Coca-Cola's $6.23B in 2024 (its $6.0B deposit with the IRS in its tax dispute)
# and $7.21B in 2025 ($6.1B of its final payment for fairlife), both as its 10-K describes, pass against a usual swing of
# $0.53B in 2019 to 2022, when its largest outflow was $0.61B. Outflows that recur don't: IMAX's $36.0M and $35.2M in
# 2023 and 2024 against $45.9M in 2019, or Teva's $1.37B in 2025 against $2.19B in 2020, which a comparison with only the
# other years of the window had taken for one-off payments.
WC_SALES = 0.05
WC_TIMES = 3
WC_YEARS = 4
WC_HISTORY = 3


def _one_off_cash(years, series, window, history=None):
    """{index into `series`: one-off payments inside that year's operating cash flow}, which the cash-flow model adds
    back to its free cash flow (report._cash_flow_model), for the model's years (the last three of `window`): the part
    of an outlying working capital outflow (WC_SALES, WC_TIMES) beyond the company's usual swing, measured over the
    years before those three, from `series` (fiscal `years`) and `history` ({year: change} of earlier years)."""
    model = window[-3:]
    if not model:
        return {}
    before = {y: v for y, v in (history or {}).items() if y < years[model[0]] and v is not None}
    before.update({years[j]: series[j]["working_capital"] for j in range(model[0])
                   if series[j].get("working_capital") is not None})
    ref = list(before.values())
    if len(ref) < WC_YEARS:
        return {}
    usual = statistics.median(abs(x) for x in ref)
    out = {}
    for i in model:
        v, rev = series[i].get("working_capital"), series[i].get("revenue")
        if v is not None and rev and rev > 0 and v > WC_SALES * rev and v > WC_TIMES * usual and v > max(ref):
            out[i] = v - usual
    return out


def _preferred_claim(L):
    """The preferred stock on the latest balance sheet `L` (_bank_parts' reading), or 0 where none is tagged or it tops
    total assets, which no real preferred stock can."""
    pref = ((L.get("quarters") or {}).get(L.get("as_of")) or {}).get("preferred") or 0
    assets = L.get("total_assets")
    return 0 if assets and pref > assets else pref


# The shortest and longest periods read as a quarter: three calendar months run 90 to 92 days, 13 and 14 weeks 91 and
# 98, 12 weeks 84 (Costco's first three quarters), and Costco's fourth quarter 16 weeks or, in a 53-week year, 17 (119).
QUARTER_DAYS = (80, 122)
# How far apart the dates of back-to-back periods, or of a fiscal year's first day and its first quarter's, may be.
SEAM_DAYS = 3
# How many days a year-ago quarter's end may be from a year before a quarter's (52 weeks is 364 days, 53 weeks 371).
YEAR_AGO_DAYS = 15
# How much longer or shorter than its year-ago quarter a quarter must be for its growth to carry the difference
# (extra_days): the fourth quarter of a 53-week year has an extra week, which alone adds about 8% to its growth.
EXTRA_DAYS = 5
# The least and most a worked-out fourth quarter may be against the average of the year's first three. Beyond them the
# year's quarters are taken not to match its annual figure and are all left blank (_quarter_series). On September 2026
# data the middle 90% of fourth quarters came to 0.53 to 1.68 times the other three.
Q4_RATIO = (0.25, 4.0)
# How far from a quarter of its fiscal year's revenue (or, for a year not yet in an annual report, the newest year's)
# a quarter's revenue may be before it is taken for a misread and left blank: Plexus tagged its quarters' revenue a
# thousand times too small in its 10-Qs from late 2023 (1.0 for about $1.0B).
QUARTER_SCALE = 10
# The most a quarter of a fiscal year not yet in an annual report may be against a quarter of the newest year's revenue.
# Nvidia's fastest growth took a quarter to 2.6 times its previous year's average; L3Harris tagged its whole 2025 revenue
# ($21.9B) as its fourth quarter's, 4.1 times, and the annual frames lack that year.
LATER_SCALE = 3
# How far four reported quarters may add up from their fiscal year's revenue before the year's quarters are taken not to
# match it and left blank.
YEAR_SUM_GAP = 0.02
# The most a worked-out fourth quarter's gross margin may differ from the average of the year's first three before its
# gross profit is left blank.
Q4_MARGIN_GAP = 0.2
# A fourth quarter is not worked out from figures rounded so coarsely that their rounding could move it by a percent: the
# year and its first three quarters all multiples of the same power of ten, at least ROUNDING_MAX of the fourth quarter.
# NextEra Energy's revenue from customer contracts, a footnote figure in round hundreds of millions ($25.8B for 2025,
# $6.0B, $6.4B and $7.4B for its first three quarters), gives a fourth quarter of $6.0B where its 10-K's year less its
# third quarter 10-Q's nine months, also rounded, gives $6.1B.
ROUNDING_MAX = 0.01
# A 10-Q gives three-month figures for its own quarter and the one a year before it; a 10-K can give them for the
# quarters of its year and the year before (a recast after a spin-off, as General Electric's and Honeywell's). A
# quarter's figure from a filing that gives it otherwise, its period more than ODD_DAYS from those (a filing's own
# period being the latest day its figures run to, and a 10-K one that gives a year ending then), is a misdated or stray
# one, and gives way to the latest figure a filing gives for the quarter as one of its own, or leaves the quarter
# blank: Ball's 10-Q
# for its first quarter of 2024 tagged its restated first quarter of 2023 ($2.98B, without its aerospace business) to
# July to September 2022 as well, beside $3.95B as filed; JBT Marel's second quarter of 2024 reads $421M from its 10-Q
# for the second quarter of 2026, against $402M as filed and as its 2025 10-Q repeated it.
ODD_DAYS = 15
# The average length of a quarter in days, and how far from a whole number of them a quarter's first day may fall from
# the first day of its fiscal year, for a year not in an annual report yet (whose start and length are taken from the
# newest year's), for the quarter to be numbered: after Ferguson moved its year end from July to December, its quarter
# from January to March 2026 would otherwise read as the third quarter of a year starting in August.
QUARTER_MEAN = 91.3
FISCAL_GRID_DAYS = 25
# How far from a whole number of average quarters a gap of two or more quarters between quarters with revenue may be
# for blank quarters to fill it (_quarter_series): Costco's missing 12-week second and third quarters run 168 days,
# 15 short of two average quarters.
BLANK_SLACK = 20


def _quarter_series(f, series, bank=False, gross=True):
    """(quarters, trailing twelve months) for derive(). Up to QUARTERS_SHOWN quarters, oldest first, back to back and
    ending with the newest, each {"start", "end", "fiscal_quarter", "revenue", "gross_profit", "derived", "yoy",
    "extra_days", "yoy_withheld"}, and "dates_estimated" on a blank quarter whose dates are estimated; a quarter the
    filings don't give soundly has None for its figures. The twelve months are {"end", "revenue", "gross_profit",
    "prior_revenue"} for the newest four quarters when all four have revenue on one footing, else None; "prior_revenue"
    is the four quarters' a year before, where each quarter has its growth.

    Each quarterly frame holds a company's latest filed figure for the quarter, and each annual frame its latest filed
    figure for the year. A quarter's revenue is read as the fiscal year's (`series`, derive's years): the first revenue
    tag the company uses (DURATION), or for a `bank` net interest income plus noninterest income (_bank_year). A quarter
    that doesn't tag its fiscal year's revenue tag counts under another only when the two agree: the same figure for
    that year and in each of its quarters that has both or, for a year without both, in every quarter and year that has
    both. A tag whose figure is zero where another's is above it doesn't count (Flowserve tags Revenues as zero beside
    its revenue from customer contracts). A REIT whose revenue includes rent tagged apart (derive) has no quarterly
    revenue, since its rent is not read quarterly. A figure from a filing other than the company's own 10-Qs and 10-Ks
    ("unmatched", _quarter_lookups: an 8-K giving a business it bought, such as Neuronetics' third quarter of 2023 in the
    frames, Greenbrook's) gives way to the latest figure its own filings give for the period, and a fiscal year from
    such a filing is not one of the company's (Amcor's 2023 in the annual frames is Berry Global's year to September).

    Companies seldom report their fourth quarter on its own, so it is worked out as the fiscal year's revenue less its
    first three quarters, where all three are there back to back from the fiscal year's first day under the same tag and
    the rest of the year is a quarter long ("derived": true). Periods are matched on their dates, so fiscal years ending
    in any month or running 52 or 53 weeks fit. A period that runs across the end of a fiscal year is no quarter of it.

    Figures are only combined where they come from one filing, or where the filings on one footing give them all as
    they are shown (_footings, from the company's own filings, "concept"), so a restatement never lands in a worked-out
    quarter or a growth rate: a fourth quarter whose year and first three quarters in the frames are not on one footing
    is worked out from the latest footing whose filings give all four figures, or left blank; growth on the year before
    is left out ("yoy_withheld": "restated" where each quarter is on some footing but not the same one, "unchecked"
    where that can't be told, the company's filings not being looked up yet or not giving a figure, and "no_base" where
    the year-ago quarter had no revenue). A fiscal
    year whose quarters don't match its annual figure has all its quarters left blank: a worked-out fourth quarter at or
    below zero or outside Q4_RATIO of the other three, four reported quarters more than YEAR_SUM_GAP from the year, or a
    quarter as large as the whole year (Comfort Systems' 2025 revenue in the frames is its first quarter's). A quarter
    more than QUARTER_SCALE from a quarter of its year's revenue (above LATER_SCALE of the newest year's, for a year not
    in an annual report yet), or equal to a year's revenue, is left blank.

    Gross profit (with `gross`) is read on the newest fiscal year's basis (_gross_profit), for the quarters of the
    years on that basis, so every quarter's is measured one way (The Hackett Group's 2023 gross profit line comes to
    29% of revenue, and its 2025 revenue less cost of sales to 38%): the gross profit line, or revenue less the same
    cost tag, from the same filing as the quarter's revenue or one that gave the same revenue. Each quarter's growth
    ("yoy") is against the quarter a year before, and "extra_days" is how many days longer (or, below zero, shorter)
    the quarter ran, where that is at least EXTRA_DAYS.
    Between two quarters with revenue, a gap as long as a whole number of quarters is filled with blank quarters, and
    any other gap (a fiscal year's end moved) starts the series again after it. Nothing is returned when the newest
    quarter ended more than two quarters before the newest fiscal year did, so an old series never passes for a current
    one."""
    D, day = dt.date.fromisoformat, dt.timedelta(days=1)
    seam = SEAM_DAYS * day
    if any(s.get("rent_revenue") for s in series):
        return [], None
    concept, unmatched = f.get("concept") or {}, f.get("unmatched") or set()
    raw = f.get("quarters") or {}
    # The SEC's companyconcept answers run days behind its frames: Coca-Cola's 10-Q for its quarter to July 3, 2026 was
    # in the frames weeks before it was in its companyconcept answer. A filing the rows lack that gives a period ending
    # after every row is such a new 10-Q or 10-K rather than another company's figures in an 8-K, so its figures in the
    # frames (its own quarter or year, and the year-ago ones it repeats) join the rows as its latest filing.
    ends = [r[1] for rs in concept.values() for r in rs]
    newer = {facts[t]["accn"] for (_, b), facts in raw.items() for t in facts
             if facts[t].get("accn") in unmatched and ends and b > max(ends)}
    newer |= {accn for x in series for accn in (x.get("revenue_accns") or {}).values()
              if accn in unmatched and ends and (x.get("end") or "") > max(ends)}
    if newer:
        concept = {t: rs + [[a, b, facts[t]["val"], facts[t]["accn"], "9999-12-31"]
                            for (a, b), facts in sorted(raw.items()) if facts.get(t, {}).get("accn") in newer]
                   + [[x["start"], x["end"], x["revenue_tags"][t], x["revenue_accns"][t], "9999-12-31"] for x in series
                      if (x.get("revenue_accns") or {}).get(t) in newer and t in (x.get("revenue_tags") or {})]
                   for t, rs in concept.items()}
        unmatched = unmatched - newer
    nii_tags, fees_tag = DURATION["bank_nii"] + DURATION["bank_nii_alt"], DURATION["bank_noninterest_income"][0]
    # Revenue footings from the revenue tags alone (a bank's two parts): a restatement of gross profit only is no
    # restatement of revenue, and gross profit has its own (gross_together).
    concept_gross = concept
    concept = {t: rs for t, rs in concept.items() if t in REVENUE_TAGS or t in nii_tags or t == fees_tag}
    footing = _footings(concept) if concept else {}

    def latest_row(tag, start, end):
        """The latest [start, end, value, accession number, filed] the company's own filings give for a period."""
        return next((r for r in reversed(concept.get(tag) or []) if (r[0], r[1]) == (start, end)), None)

    # Each filing's own period: the latest day its figures run to, and whether it gives a year ending then (a 10-K).
    period = {}
    for rs in (f.get("concept") or {}).values():
        for r in rs:
            e, yearly = D(r[1]), (D(r[1]) - D(r[0])).days >= 300
            end, annual = period.get(r[3], (e, yearly))
            period[r[3]] = (max(end, e), annual or yearly) if e <= end else (e, yearly)

    def stray(accn, b):
        """Whether a filing gives a quarter ending on `b` other than as one of its own (ODD_DAYS)."""
        if accn not in period:
            return False
        gap = (period[accn][0] - b).days
        if period[accn][1]:
            return not -ODD_DAYS <= gap <= 2 * 371 + ODD_DAYS
        return min(abs(gap), abs(gap - 364), abs(gap - 371)) > ODD_DAYS

    def own_row(tag, start, end):
        """The latest row the company's own filings give for a quarter as one of their own."""
        return next((r for r in reversed(concept.get(tag) or []) if (r[0], r[1]) == (start, end)
                     and not stray(r[3], D(end))), None)

    fys = []
    for s in series:
        if s.get("start") and s.get("end") and s.get("revenue") is not None:
            a, b = D(s["start"]), D(s["end"])
            accns = s.get("revenue_accns") or {}
            if bank:
                nii = next((t for t in nii_tags if t in accns), None)
                parts = [nii] if bank == "nii" else [nii, fees_tag]
                own = {"bank": {accns[t] for t in parts}} if nii and all(t in accns for t in parts) else {}
            else:
                own = {t: {accns.get(t)} for t in (s.get("revenue_tags") or {})}
            mine = own.get("bank" if bank else s.get("revenue_tag"), set())
            if 350 <= (b - a).days + 1 <= 380 and not mine & unmatched:
                fys.append({"a": a, "b": b, "revenue": s["revenue"], "tag": "bank" if bank else s.get("revenue_tag"),
                            "tags": s.get("revenue_tags") or {}, "accns": own,
                            "gross_profit": s.get("gross_profit") if gross else None,
                            "basis": s.get("gross_basis") if gross else None})
    if not fys or not raw:
        return [], None
    newest = max(fys, key=lambda y: y["b"])
    # Fiscal years the annual frames lack, from the company's own filings (_quarter_lookups), which only work out their
    # fourth quarter: under the newest year's revenue tag, with gross profit on its basis.
    for (start, end), vals in (f.get("fy_extra") or {}).items():
        a, b = D(start), D(end)
        tag, basis = newest["tag"], newest["basis"]
        if bank or tag not in vals or b > newest["b"] or any(a <= y["b"] - seam and b >= y["a"] + seam for y in fys):
            continue
        cost = vals[basis]["val"] if basis and basis != "GrossProfit" and basis in vals else None
        fys.append({"a": a, "b": b, "revenue": vals[tag]["val"], "tag": tag, "tags": {tag: vals[tag]["val"]},
                    "accns": {tag: {vals[tag]["accn"]}}, "basis": basis,
                    "gross_profit": vals["GrossProfit"]["val"] if basis == "GrossProfit" and "GrossProfit" in vals
                    and vals["GrossProfit"]["accn"] == vals[tag]["accn"] else vals[tag]["val"] - cost
                    if cost is not None and vals[basis]["accn"] == vals[tag]["accn"] else None})
    fys.sort(key=lambda y: y["a"])

    def fiscal_year(p):
        """(the fiscal year a period falls in, one of fys, or None; whether it is instead the newest one, for a period
        after it whose year is not in an annual report yet)."""
        y = next((y for y in fys if p["start"] >= y["a"] - seam and p["end"] <= y["b"] + seam), None)
        return (y, False) if y else (newest, True) if p["start"] > newest["b"] else (None, False)

    # Where fiscal years end: those in the annual reports, and the day before the oldest begins.
    year_ends = [y["b"] for y in fys] + [fys[0]["a"] - day]

    def pairs_of(rows):
        """{(tag, tag): whether the two gave the same figure, within half a percent, in every one of `rows` (each
        {tag: value}) that has both}."""
        out = {}
        for vals in rows:
            items = [(t, v) for t, v in vals.items() if t in REVENUE_TAGS]
            for i, (t1, v1) in enumerate(items):
                for t2, v2 in items[i + 1:]:
                    key = tuple(sorted((t1, t2)))
                    out[key] = out.get(key, True) and abs(v1 - v2) <= 0.005 * max(abs(v1), abs(v2))
        return out

    if not bank:
        # Two revenue tags agree for a fiscal year when both gave the same figure for the year and in each of its
        # quarters (and, for the newest year, those after it) that has both: Sonoco's revenue from customer contracts
        # was its whole revenue in 2025, the only tag its 2025 quarters use, but not in 2022. For a year without both
        # tags, they must have given the same figure in every quarter and year that has both, and in at least one (ADP
        # tags its quarters one way and its years both ways).
        vals = lambda facts: {t: v["val"] for t, v in facts.items()}
        overall = pairs_of([vals(facts) for facts in raw.values()] + [y["tags"] for y in fys])
        yearly = {}
        for y in fys:
            inside = [vals(facts) for (a, b), facts in raw.items()
                      if D(a) >= y["a"] - seam and (D(b) <= y["b"] + seam or y is newest and D(a) > y["b"])]
            yearly[id(y)] = pairs_of([y["tags"]] + inside)

        def agree(t1, t2, y=None):
            key = tuple(sorted((t1, t2)))
            if t1 == t2:
                return True
            if y is not None and t1 in y["tags"] and t2 in y["tags"]:
                return yearly[id(y)].get(key, False)
            return overall.get(key, False)
    else:
        agree = lambda t1, t2, y=None: True

    periods = []
    for (start, end), facts in raw.items():
        a, b = D(start), D(end)
        if not QUARTER_DAYS[0] <= (b - a).days + 1 <= QUARTER_DAYS[1] \
                or any(a <= e - seam and b >= e + seam for e in year_ends):
            continue
        if bank:
            nii = next((t for t in nii_tags if t in facts), None)
            tags = [nii] if bank == "nii" and nii else [nii, fees_tag] if nii and fees_tag in facts else []
            rev = sum(facts[t]["val"] for t in tags) if tags else None
            tag, accns = "bank", {facts[t].get("accn") for t in tags} if tags else {None}
            if any(stray(accn, b) for accn in accns):
                continue
        else:
            # The fiscal year's own tag, else one that agrees with it, so a quarter that also tags another revenue
            # figure still counts: Sempra's 2025 quarters tag its operating revenue as utilities do, and its revenue
            # from customer contracts, a smaller figure first in DURATION, beside it.
            y = fiscal_year({"start": a, "end": b})[0]
            above = any(facts[t]["val"] > 0 for t in REVENUE_TAGS & facts.keys())
            tag = next((t for t in REVENUE_ORDER if t in facts and (facts[t]["val"] or not above)
                        and (y is None or agree(t, y["tag"], y))), None)
            rev = facts[tag]["val"] if tag else None
            accns, tags = {facts[tag].get("accn")} if tag else set(), [tag]
            row = latest_row(tag, start, end) if accns & unmatched else None
            if tag and any(stray(accn, b) for accn in accns):
                row = own_row(tag, start, end)
                if row is None:
                    continue
            if row is not None:
                rev, accns = row[2], {row[3]}
                facts = {**facts, tag: {"val": row[2], "accn": row[3]}}
        if rev is not None:
            periods.append({"start": a, "end": b, "revenue": rev, "tag": tag, "facts": facts, "derived": False,
                            "accns": accns, "tags": tags})
    periods.sort(key=lambda p: (p["end"], p["start"]))

    # Two periods that overlap by more than a seam are two readings of one quarter. The one under its fiscal year's
    # revenue tag counts, where only one is (Waters' 2025 first quarter, to March 31, beside a business it was buying
    # tagged to March 29 under another tag); otherwise which is right can't be told, and neither counts.
    drop = set()
    for i, (p, q) in enumerate(zip(periods, periods[1:])):
        if q["start"] <= p["end"] - seam:
            y = fiscal_year(p)[0] or fiscal_year(q)[0]
            own = [bool(y and not bank and y["tag"] in x["facts"]) for x in (p, q)]
            drop |= {i + 1} if own == [True, False] else {i} if own == [False, True] else {i, i + 1}
    periods = [p for i, p in enumerate(periods) if i not in drop]

    def scaled(p):
        (y, later), r = fiscal_year(p), p["revenue"]
        if any(abs(r - x["revenue"]) <= 0.001 * abs(x["revenue"]) for x in fys if abs(x["revenue"]) >= 1e7):
            return False  # a year's revenue tagged as a quarter's (L3Harris' 2024)
        return y is None or y["revenue"] <= 0 \
            or 1 / QUARTER_SCALE <= r / (y["revenue"] / 4) <= (LATER_SCALE if later else QUARTER_SCALE)
    periods = [p for p in periods if scaled(p)]

    # Each footing's figure for each tag and period: the latest filing on that footing that gives it, which every other
    # filing on it agrees with (_footings).
    within, footings = {}, sorted(set(footing.values()), reverse=True)
    for t, rs in concept.items():
        for start, end, val, accn, _ in rs:
            within[(footing[accn], t, start, end)] = (val, accn)  # rows run oldest filing first, so the latest wins

    def on(x, fo):
        """A figure (a quarter, a year, or a fourth quarter worked out from its year and first three quarters) as the
        filings on footing `fo` give it, with the filings, or None where they don't give every part of it."""
        got, accns = [], set()
        for a, b in x.get("parts") or [(x["start"], x["end"])]:
            vals = [within.get((fo, t, a.isoformat(), b.isoformat())) for t in x["tags"]]
            if not vals or None in vals:
                return None
            got.append(sum(v for v, _ in vals))
            accns |= {accn for _, accn in vals}
        return (got[0] - sum(got[1:]) if x.get("parts") else got[0]), accns

    def shown_on(x, fo):
        """Whether the filings on footing `fo` give a figure as it is shown (a worked-out one within half a percent)."""
        v = on(x, fo)
        return v is not None and abs(v[0] - x["revenue"]) <= (0.005 if x.get("parts") else RESTATED_GAP) * abs(
            x["revenue"])

    def consistent(figures):
        """Whether figures (periods, years and worked-out fourth quarters) may be combined: all from one filing, or
        all given as they are shown by the filings on one footing (_footings). The second holds for a quarter and its
        year-ago quarter from the 10-Q that first gave both, though a restatement of other periods came between."""
        accns = set().union(*(x["accns"] for x in figures))
        if None not in accns and len(accns) == 1:
            return True
        return any(all(shown_on(x, fo) for x in figures) for fo in footings)

    def restated(figures):
        """Whether figures that are not consistent are each on some footing, so that a restatement stands between them,
        rather than their filings being unknown (not looked up yet, or not the company's own)."""
        return bool(footings) and all(any(shown_on(x, fo) for fo in footings) for x in figures)

    gross_memo = {}

    def gross_together(basis, items):
        """Whether gross profit figures (`items`, [(start, end, value)] under `basis`, the gross profit tag or the cost
        tag) are all given as they are by the filings on one footing of that tag (_footings)."""
        rows = concept_gross.get(basis) or []
        if basis not in gross_memo:
            fo = _footings({basis: rows})
            gross_memo[basis] = (set(fo.values()), {(fo[r[3]], r[0], r[1]): r[2] for r in rows})
        footings_g, at = gross_memo[basis]
        return bool(items) and any(all(abs(at.get((x, a.isoformat(), b.isoformat()), math.inf) - v)
                                       <= RESTATED_GAP * abs(v) for a, b, v in items) for x in footings_g)

    def refooted(q4):
        """A fourth quarter worked out from its year and first three quarters ("parts", "tags") as the latest footing
        whose filings give all four figures has them, with those filings and the quarters' figures, or None. Each
        quarter must be within QUARTER_SCALE of a quarter of the year, as scaled() asks of the frames' quarters: Primo
        Brands' holding company, before it took over the business, filed a 10-Q giving its own third quarter of 2024 as
        zero, beside the business's year and first two quarters."""
        for fo in footings:
            v = on(q4, fo)
            if v is not None:
                parts = [on({"start": a, "end": b, "tags": q4["tags"]}, fo)[0] for a, b in q4["parts"][1:]]
                year = v[0] + sum(parts)
                if year > 0 and all(1 / QUARTER_SCALE <= x / (year / 4) <= QUARTER_SCALE for x in parts):
                    return v[0], v[1], parts
        return None

    # Fourth quarters worked out from the year, and years whose quarters don't match their annual figure.
    derived, mismatched = [], []
    for y in fys:
        inside = [p for p in periods if p["start"] >= y["a"] - seam and p["end"] <= y["b"] + seam]
        first3, at = [], y["a"]
        for p in inside:
            if len(first3) < 3 and abs((p["start"] - at).days) <= SEAM_DAYS:
                first3.append(p)
                at = p["end"] + day
        rest = [p for p in inside if first3 and p["start"] > first3[-1]["end"]]
        # The year's revenue under the tag its quarters use, where they all use one the year tags too (an agreeing
        # tag's figure can differ by up to half a percent, which would all land in a worked-out fourth quarter).
        used = {p["tag"] for p in first3 + rest}
        tag = next(iter(used)) if len(used) == 1 and not bank and next(iter(used)) in y["tags"] else y["tag"]
        whole = y["revenue"] if bank else y["tags"].get(tag, y["revenue"])
        tags = first3[0]["tags"] if first3 else []
        year = {"start": y["a"], "end": y["b"], "revenue": whole, "accns": y["accns"].get(tag) or {None},
                "tags": tags if bank else [tag]}
        if y["revenue"] > 0 and any(p["revenue"] >= 0.999 * y["revenue"] for p in inside):
            mismatched.append(y)  # a quarter as large as the whole year (NiSource's year tagged as its fourth quarter)
        elif len(first3) == 3 and len(rest) == 1 and abs((rest[0]["start"] - at).days) <= SEAM_DAYS \
                and abs((rest[0]["end"] - y["b"]).days) <= SEAM_DAYS:
            if abs(sum(p["revenue"] for p in first3 + rest) - whole) > YEAR_SUM_GAP * abs(whole):
                mismatched.append(y)
        elif len(first3) == 3 and not rest:
            a, b = at, y["b"]
            if not QUARTER_DAYS[0] <= (b - a).days + 1 <= QUARTER_DAYS[1] \
                    or any(not agree(p["tag"], y["tag"], y) for p in first3) \
                    or any(p["start"] <= b - seam and p["end"] >= a + seam for p in periods):
                continue
            # One set of tags for the year and all three quarters, which the filings on a footing are read under.
            q4 = {"start": a, "end": b, "derived": True, "tags": tags,
                  "parts": [(y["a"], y["b"])] + [(p["start"], p["end"]) for p in first3]}
            uniform = all(x["tags"] == tags for x in first3 + [year])
            if consistent(first3 + [year]):
                value, accns, parts = whole - sum(p["revenue"] for p in first3), year["accns"].union(
                    *(p["accns"] for p in first3)), [p["revenue"] for p in first3]
            else:
                value, accns, parts = uniform and refooted(q4) or (None, None, [0])
                if value is None or value <= 0 or sum(parts) <= 0 \
                        or not Q4_RATIO[0] <= value / (sum(parts) / 3) <= Q4_RATIO[1] \
                        or _grain([value + sum(parts)] + parts) >= ROUNDING_MAX * value:
                    continue  # left blank: its year and quarters as the filings last gave them don't fit together
                derived.append({**q4, "revenue": value, "gross_profit": None, "accns": accns})
                continue
            mean = sum(parts) / 3
            if value <= 0 or mean <= 0 or not Q4_RATIO[0] <= value / mean <= Q4_RATIO[1]:
                mismatched.append(y)
                continue
            if _grain([whole] + parts) >= ROUNDING_MAX * value:
                continue
            gps = [_quarter_gross(p, y["basis"], concept) if y["basis"] == newest["basis"] else None for p in first3]
            # The year's gross profit less the first three quarters', on the year's basis: its gross profit line, or
            # its revenue (under the quarters' tag, as above) less its cost of revenue.
            gp = None if y["gross_profit"] is None or None in gps else \
                y["gross_profit"] - sum(gps) if y["basis"] == "GrossProfit" else \
                whole - (y["revenue"] - y["gross_profit"]) - sum(gps)
            margins = [g / p["revenue"] for g, p in zip(gps, first3) if g is not None and p["revenue"] > 0]
            # The year's gross profit line (or cost) and its first three quarters' on one footing (gross_together).
            basis = y["basis"]
            gitems = [] if gp is None else [(y["a"], y["b"], y["gross_profit"] if basis == "GrossProfit"
                                              else y["revenue"] - y["gross_profit"])] \
                + [(p["start"], p["end"], p["facts"][basis]["val"]) for p in first3]
            if gp is not None and (gp > value or len(margins) < 3 or not gross_together(basis, gitems)
                                   or abs(gp / value - sum(margins) / 3) > Q4_MARGIN_GAP):
                gp = None
            derived.append({**q4, "revenue": value, "gross_profit": gp, "accns": accns, "basis": basis,
                            "gitems": gitems if gp is not None else []})
    periods = [p for p in periods if fiscal_year(p)[1] or fiscal_year(p)[0] not in mismatched]
    for p in periods:
        y = fiscal_year(p)[0]
        p["gross_profit"] = _quarter_gross(p, y["basis"], concept) if y and y["basis"] == newest["basis"] else None
        p["basis"] = y["basis"] if y else None
        p["gitems"] = [(p["start"], p["end"], p["facts"][p["basis"]]["val"])] if p["gross_profit"] is not None else []
    periods = sorted(periods + derived, key=lambda p: p["end"])
    if not periods or periods[-1]["end"] < newest["b"] - 183 * day:
        return [], None

    # Back to back, with blank quarters in each gap as long as a whole number of quarters: one quarter long
    # (QUARTER_DAYS), or within BLANK_SLACK days of two or more average quarters. Where a gap holds a fiscal year's end,
    # the blank quarters on either side of it end and begin there; any other dates in a gap of two or more quarters are
    # estimated. A gap of any other length (a fiscal year's end moved, with a stub period between: Red Cat's 14 months
    # from November 2023, when it moved its year end from April to December) starts the series again after it.
    slots = []
    for p in periods[-(QUARTERS_SHOWN + 4):]:
        if slots:
            gap = (p["start"] - slots[-1]["end"]).days - 1
            n = round(gap / QUARTER_MEAN) if gap > SEAM_DAYS else 0
            if gap > SEAM_DAYS and not (n == 1 and QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1]
                                        or n > 1 and abs(gap - n * QUARTER_MEAN) <= BLANK_SLACK):
                slots = []
            elif gap > SEAM_DAYS:
                first, last = slots[-1]["end"] + day, p["start"] - day
                # The last day of each blank quarter but the last: a fiscal year's end near where it would fall, or an
                # estimate.
                ends = []
                for k in range(1, n):
                    guess = first + dt.timedelta(days=round(k * (gap + 1) / n)) - day
                    near = [e for e in year_ends if abs((e - guess).days) <= QUARTER_MEAN / 2]
                    ends.append((min(near, key=lambda e: abs((e - guess).days)), False) if near else (guess, True))
                ends.append((last, False))
                for k, (b, guessed) in enumerate(ends):
                    slots.append({"start": slots[-1]["end"] + day, "end": b, "revenue": None, "gross_profit": None,
                                  "derived": False, "accns": {None},
                                  "estimated": guessed or k > 0 and ends[k - 1][1]})
        slots.append(p)
    # Fiscal quarter numbers, counted from the start of the fiscal year a quarter falls in: the first days of the years
    # in the annual reports and of those right after them, with any years beyond taken to run as long as the nearest,
    # where the quarter lines up with them (FISCAL_GRID_DAYS).
    starts = sorted({y["a"] for y in fys} | {y["b"] + day for y in fys})
    while starts[0] > slots[0]["start"]:
        starts.insert(0, starts[0] - (fys[0]["b"] - fys[0]["a"] + day))
    while starts[-1] <= slots[-1]["start"]:
        starts.append(starts[-1] + (newest["b"] - newest["a"] + day))

    def number(s):
        y, later = fiscal_year(s)
        if y is not None and not later:
            base, strict = y["a"], False
        else:
            begun = [x for x in starts if x <= s["start"] + 10 * day]
            if not begun:
                return None
            base, strict = begun[-1], True
        off = (s["start"] - base).days
        n = round(off / QUARTER_MEAN)
        return n + 1 if 0 <= n <= 3 and not (strict and abs(off - n * QUARTER_MEAN) > FISCAL_GRID_DAYS) else None

    out, agos = [], []
    for s in slots:
        ago = next((x for x in slots if abs((s["end"] - x["end"]).days - 365) <= YEAR_AGO_DAYS), None)
        yoy = extra = withheld = None
        if ago is not None and s["revenue"] is not None and ago["revenue"] is not None:
            if ago["revenue"] <= 0:
                withheld = "no_base"
            elif consistent([s, ago]):
                yoy = s["revenue"] / ago["revenue"] - 1
                extra = (s["end"] - s["start"]).days - (ago["end"] - ago["start"]).days
            else:
                withheld = "restated" if restated([s, ago]) else "unchecked"
        agos.append(ago if yoy is not None else None)
        out.append({"start": s["start"].isoformat(), "end": s["end"].isoformat(), "fiscal_quarter": number(s),
                    "revenue": s["revenue"], "gross_profit": s["gross_profit"], "derived": s["derived"], "yoy": yoy,
                    "extra_days": extra if extra is not None and abs(extra) >= EXTRA_DAYS else None,
                    "yoy_withheld": withheld, **({"dates_estimated": True} if s.get("estimated") else {})})
    out = out[-QUARTERS_SHOWN:]
    while out and out[0]["revenue"] is None:
        out.pop(0)
    recent, ttm = slots[-4:], None
    if len(recent) == 4 and all(s["revenue"] is not None for s in recent) and consistent(recent) \
            and 350 <= (recent[-1]["end"] - recent[0]["start"]).days + 1 <= 380:
        # Gross profit for the twelve months where each quarter's is known, from gross profit figures on one footing.
        gps = [s["gross_profit"] for s in recent]
        if None not in gps and (len({s.get("basis") for s in recent}) > 1
                                or not gross_together(recent[0].get("basis"), [x for s in recent for x in s["gitems"]])):
            gps = [None]
        before = agos[-4:]
        ttm = {"end": recent[-1]["end"].isoformat(), "revenue": sum(s["revenue"] for s in recent),
               "gross_profit": sum(gps) if None not in gps else None,
               "prior_revenue": sum(x["revenue"] for x in before)
               if None not in before and len({id(x) for x in before}) == 4 else None}
    return out, ttm


def _grain(values):
    """The largest power of ten that every one of `values` (whole numbers of dollars) is a multiple of."""
    g = 1
    while g < 1e12 and all(round(v) % (g * 10) == 0 for v in values):
        g *= 10
    return g


def _quarter_gross(p, basis, concept=None):
    """A quarter's gross profit on a fiscal year's basis (_gross_profit's gross_basis), or None: its gross profit line,
    or its revenue less the cost tag named, never above its revenue, and from the same filing as its revenue or one
    that gave the same revenue (_gave, from the company's own filings `concept`)."""
    if not basis or basis not in p["facts"]:
        return None
    facts, accn = p["facts"], p["facts"][basis].get("accn")
    # Its filing's revenue, where the frames have it under another tag, or else its own filings.
    same = lambda t, d: t in REVENUE_TAGS and d.get("accn") == accn \
        and abs(d["val"] - p["revenue"]) <= RESTATED_GAP * abs(p["revenue"])
    if accn not in p["accns"] and not any(same(t, d) for t, d in facts.items()) \
            and not _gave(concept or {}, p["start"].isoformat(), p["end"].isoformat(), accn, p["revenue"]):
        return None
    if basis == "GrossProfit":
        v = _val(facts.get("GrossProfit"))
        if v is not None and _off_footing(facts, p["revenue"], v):
            return None
    else:
        cost = _val(facts.get(basis))
        v = p["revenue"] - cost if cost is not None and cost >= 0 else None
    return v if v is not None and v <= p["revenue"] else None


def derive(f, tax_rate=OWN_RATE, bank=False, check_debt=True, gross=True, lender=False):
    """Turns raw line items into the ratios the screener and reports use. Missing data stays None.

    Earnings-based measures (the adj_ keys and profitable_years) leave out one-time items. `tax_rate` is the
    normal rate those earnings are taxed at: 0 for a REIT, which pays no corporate income tax on what it pays out,
    None for a company whose tax is normally below nothing, or OWN_RATE for the company's own steady rate where it
    has one and the statutory rate otherwise (see _adjust and _normal_rate).

    A REIT also gets its funds from operations and the measures built on them (_ffo, _reit_fields), and its rent
    stands in for a sales figure it doesn't tag. Every other company gets "reit": False.

    A `bank` (report.is_bank) takes its revenue as banks count it (_bank_year), whatever its revenue tags say, and gets
    the measures analysts judge banks by (_bank_fields). Every other company gets "bank": False.

    A `lender` (a financial company, as build.py tells) or a REIT that tags no revenue line takes its revenue from a
    lender's income statement lines instead, and a business development company's is its total investment income
    (DURATION): "revenue_basis" says which ("bank", "net_interest" or "investment_income"; None for every other
    company).
    """
    years = sorted(f["annual"])
    series = [f["annual"][y] for y in years]
    if not bank:
        # Gains or losses on stakes in other companies under the broader tags, where none is tagged under SECURITIES
        # (_items' other_securities; a bank reads its own, bank_securities, below).
        # Not where it is the year's gain or loss on selling a business or assets, tagged under both, which would then come
        # out twice (Medallion Financial's 2025 $24.6M gain on selling a stake, tagged as a disposal of an equity-method
        # investment and as a gain on investments in the same 10-K).
        series = [dict(s, securities=s["other_securities"]) if s.get("securities") == 0 and s.get("other_securities")
                  and not (s.get("sale") and abs(s["other_securities"] - s["sale"]) <= 0.001 * abs(s["sale"]))
                  else s for s in series]
    if bank:
        # A bank whose net income has no frame for a year it tagged its pretax income and tax for has what they leave
        # (Esquire Financial's 2025: $65.7M less $14.8M of tax, $50.8M as its 10-K shows).
        series = [dict(s, net_income=s["pretax"] - s["income_tax"] + (s.get("discontinued") or 0)
                       - (s.get("minority") or 0), common_gap=max(s.get("common_gap") or 0, s.get("preferred") or 0))
                  if s.get("net_income") is None and s.get("pretax") is not None and s.get("income_tax") is not None
                  else s for s in series]
        # A bank that tags no securities gains or losses under SECURITIES may under its other tags (_bank_year), and one
        # that tags no pretax income (13 banks, among them BNY, Zions and Columbia Banking) has what its net income and
        # tax add up to, as a REIT does (_reit_year), so its one-time items can be read (Simmons First's 2025 loss of
        # $801M on selling securities, which turned a year's profit into a $398M loss).
        series = [dict(s, revenue=s.get("bank_revenue"),
                       securities=s["bank_securities"] if s.get("securities") == 0 and s.get("bank_securities") else
                       s.get("securities"),
                       **({"pretax": s["net_income"] + s["income_tax"] - (s.get("discontinued") or 0)
                           + (s.get("minority") or 0), "pretax_has_equity_method": True}
                          if s.get("pretax") is None and s.get("net_income") is not None
                          and s.get("income_tax") is not None else {})) for s in series]
        # Its noninterest income, where it tags none, is then what that pretax income implies, as in _bank_year
        # (Banner's 2025, which tags neither: net income of $195.4M plus $43.5M of tax, less $574.9M of net interest
        # income after the provision, plus $408.8M of noninterest expense: $72.8M, as its 10-K shows).
        series = [_bank_fees(s) if s.get("revenue") is None else s for s in series]
        # A bank books its write-downs among its costs, so its pretax income stands in for the operating income _adjust()
        # checks their add-backs against: they may lift the year's pretax margin at most MARGIN_ROOM above its best other
        # year's (Kentucky First Federal's $13.6M of write-downs tagged for its year to June 2025, when all its revenue
        # came to $8.8M). Nor is the gap between its pretax income and whatever it tags as operating income an untagged
        # gain (Regions' 2022: $2.8B of its $2.9B of pretax income).
        series = [dict(s, operating_income=s.get("pretax")) for s in series]
    reit = tax_rate == 0
    if reit:
        # Some REITs tag their rent as their only sales figure (Equity Residential, American Homes 4 Rent, One Liberty),
        # or tag as sales only what they earn besides rent, which the rules for sales to customers leave out (Camden
        # Property's $13.0M in 2025, beside $1.57B of rent), so their rent is added. A sales figure close to the rent
        # is the total, net of lease adjustments (Agree Realty's $718M, against $738M of rent). Only a landlord's:
        # rent of at least LEASE_MIN of its assets. A mortgage REIT's rent from the odd property it took over is a
        # sliver of what it earns in interest (Granite Point's $12.5M in 2025, on $2B of assets).
        assets = (f.get("latest") or {}).get("total_assets") or 0
        rent = lambda s: s.get("lease_income") or 0
        series = [dict(s, revenue=rent(s) + (s.get("revenue") or 0), rent_revenue=True)
                  if (s.get("revenue") or 0) < 0.5 * rent(s) and assets > 0 and rent(s) >= LEASE_MIN * assets else s
                  for s in series]
    basis = None
    if (lender or reit) and not bank and all(s.get("revenue") is None for s in series):
        # A lender that tags no revenue line at all has its revenue as a bank counts it (_bank_year), from the lines of a
        # lender's income statement it tags: net interest income plus noninterest income (Synchrony Financial's 2025:
        # $18.47B plus $0.52B, as its 10-K shows), or for a REIT, where it tags no noninterest income, net interest
        # income alone, which is how a mortgage REIT's income statement opens (Annaly's 2025: $1.14B; AGNC's $675M). A
        # lender outside real estate needs both parts, since net interest income alone can be a sliver of a business
        # that earns mostly fees (Nelnet's loan servicing). The newest year with results sets which, and every year is
        # read that one way, so growth never compares two (Redwood Trust tagged noninterest income until 2023 only).
        newest = next((s for s in reversed(series) if s.get("net_income") is not None), {})
        if newest.get("bank_revenue") is not None:
            basis, key = "bank", "bank_revenue"
        elif reit and newest.get("bank_nii") is not None:
            basis, key = "net_interest", "bank_nii"
        if basis:
            series = [dict(s, revenue=s.get(key)) for s in series]
    i_last = next((i for i in reversed(range(len(series)))
                   if series[i].get("revenue") is not None and series[i].get("net_income") is not None), None)
    if i_last is None:
        return None
    # Every company is measured over the four fiscal years ending with its newest one.
    ys, ss = years[max(0, i_last - 3): i_last + 1], series[max(0, i_last - 3): i_last + 1]
    last, prev = ss[-1], ss[-2] if len(ss) > 1 else {}
    L = f["latest"]
    if basis is None and last.get("revenue_tag") == INVESTMENT_INCOME:
        basis = "investment_income"  # a business development company (DURATION's revenue tags)
    if basis and any(s.get("net_income") is not None for s in series[i_last + 1:]):
        # A later year's results give no revenue read this way, so its newest figures would be years old (Nelnet's
        # net interest income and noninterest income, both tagged for 2022 and only the first since).
        return None
    if basis:
        # A lender's debt and cash are its business, as a bank's are, and gross profit means nothing for it.
        check_debt = gross = False

    lt_debt = L.get("lt_debt") or 0
    total_debt = L.get("total_debt") or 0
    debt_known, debt_doubt = True, None
    if reit:
        debt, debt_known = _reit_debt(L)
        if debt != total_debt:
            lt_debt, total_debt = max(0, debt - (total_debt - lt_debt)), debt
    elif not bank and check_debt:
        debt_doubt = _debt_check(L, last.get("interest_paid"))
        debt_known = debt_doubt is None
    cash = (L.get("cash") or 0) + (L.get("st_investments") or 0) + (L.get("lt_securities") or 0)
    equity = L.get("equity")
    rev = last["revenue"]
    ni = last["net_income"]
    window = list(range(max(0, i_last - 3), i_last + 1))
    one_off_cash = _one_off_cash(years, series, window, f.get("wc_history"))
    abroad = not (f.get("loc") or "US").startswith("US")
    # A REIT that lends and a business development company hold their loans and securities as the business, marked to
    # market with the hedges against them, so gains and losses on those investments are part of their results, as for
    # the lenders they are compared with: Annaly's 2025 $1.75B gain on its investments came beside a $0.49B loss on its
    # hedges, which no tag read here marks as one-time, and taking out the gain alone left $0.28B of its $2.03B of profit.
    portfolio = basis in ("net_interest", "investment_income") or basis == "bank" and reit
    found = _adjust(series, window, max(cash, L.get("peak_cash") or 0), tax_rate, abroad, portfolio)
    adjusted = [found[j] for j in window]
    adj_ni, one_time = adjusted[-1]
    tax_normal, tax_basis = _normal_rate(series, i_last, window, tax_rate)

    ffo = _ffo(series, window, found) if reit else {}
    # Gross profit means nothing for a bank, an insurer or another financial company (`gross` is False), nor for a REIT,
    # so none is read for them.
    gross = gross and not bank and not reit
    hist = [{"year": y, "revenue": s.get("revenue"), "net_income": s.get("net_income"), "fcf": s.get("fcf"),
             **({"ffo": ffo[j][0], "adj_ffo": ffo[j][1]} if reit else {}),
             **({"gross_profit": s.get("gross_profit")} if gross else {})}
            for j, y, s in zip(window, ys, ss) if s.get("revenue") is not None or s.get("net_income") is not None]
    # A lender's quarters are read the way its years are (revenue_basis).
    quarters, ttm = _quarter_series(f, series[:i_last + 1], True if bank or basis == "bank" else
                                    "nii" if basis == "net_interest" else False, gross)
    recent3 = [a for a, _ in adjusted[-3:] if a is not None]
    if bank:
        # A bank's gains and losses on investment securities are part of its noninterest income, so the one-time ones
        # (_adjust) come out of the revenue its growth and efficiency are measured on, as banks show it themselves
        # (Truist's 2024 revenue of $13.3B included a $6.65B loss on selling securities to reinvest at higher rates).
        ss = [dict(s, revenue=_core_revenue(s.get("revenue"), items)) for s, (_, items) in zip(ss, adjusted)]
        last, prev = ss[-1], ss[-2] if len(ss) > 1 else {}
    elif basis == "bank":
        # So are a lender's (revenue_basis "bank"), and its one-time gains and losses on selling a business or assets,
        # booked among its other income, where they fit inside it: Synchrony Financial's 2024 revenue of $19.53B
        # included the $1.07B gain on selling Pets Best, so without it 2025's $18.99B was growth of 2.8%, not a fall.
        ss = [dict(s, revenue=_core_revenue(s.get("revenue"), [i for i in items or [] if not i["kind"].startswith("sale")
                                                                or i["amount"] <= abs(s.get("bank_noninterest_income")
                                                                                      or 0)], sales=True))
              for s, (_, items) in zip(ss, adjusted)]
        last, prev = ss[-1], ss[-2] if len(ss) > 1 else {}
    revs = [(y, s["revenue"]) for y, s in zip(ys, ss) if s.get("revenue")]
    core = last.get("revenue")
    growth_1y = (core / prev["revenue"] - 1) if prev.get("revenue") and prev["revenue"] > 0 and core is not None else None
    cagr = None
    if len(revs) >= 3 and revs[0][1] > 0 and revs[-1][1] > 0:
        cagr = (revs[-1][1] / revs[0][1]) ** (1 / (revs[-1][0] - revs[0][0])) - 1
    # Only back-to-back years are compared, so a missing year never reads as one year of growth.
    pairs = [(a, b) for (ya, a), (yb, b) in zip(revs, revs[1:]) if yb == ya + 1]

    dil = None
    d_now, d_prev = last.get("diluted_shares"), prev.get("diluted_shares")
    if d_now and d_prev:
        dil = d_now / d_prev - 1

    op = f["annual"][ys[-1]].get("operating_income")  # as tagged, where a bank's series holds its pretax income
    return {
        "fiscal_year": ys[-1],
        "fiscal_year_end": last.get("end"),
        "balance_as_of": L.get("as_of"),
        "revenue": rev,
        "net_income": ni,
        "operating_income": op,
        "fcf": last.get("fcf"),
        "fcf_history": [s["fcf"] for s in ss[-3:] if s.get("fcf") is not None],
        # The same years' figures behind the cash-flow model (report._cash_flow_model).
        "cash_flow_years": [{"year": y, "revenue": s.get("revenue"), "fcf": s["fcf"],
                             **{k: s.get(k) for k in CASH_FLOW_METRICS},
                             "interest_paid_tagged": s.get("interest_paid_tagged", True),
                             "one_off_cash": one_off_cash.get(j, 0.0)}
                            for j, y, s in list(zip(window, ys, ss))[-3:] if s.get("fcf") is not None],
        "net_margin": (ni / rev) if rev else None,
        "op_margin": (op / rev) if rev and op is not None else None,
        "equity": equity,
        "lt_debt": lt_debt,
        "total_debt": total_debt,
        "debt_reported": bool(L.get("debt_reported")),
        # False for a REIT whose debt could not be read (_reit_debt), or another company whose debt read looks far too
        # small (_debt_check, which says why in debt_doubt), whose net cash and debt measures then mean nothing.
        "debt_known": debt_known,
        "debt_doubt": debt_doubt,
        "cash": cash,
        "net_cash": cash - total_debt,
        # Cash and short-term investments alone, without the long-term securities `cash` counts: the money on hand
        # within a year, which the screener's "Under 1x cash" list weighs against the company's debt and market value.
        # None where the balance sheet tags neither.
        "cash_and_st_investments": None if L.get("cash") is None and L.get("st_investments") is None
        else (L.get("cash") or 0) + (L.get("st_investments") or 0),
        # Whether `equity` includes minority holders' stakes in subsidiaries: a company that tags only that total (no
        # StockholdersEquity of its own) is read from it, as UWM Holdings, whose founder's units in its operating company
        # were $851M of its $985M at June 2026. The screener's book value then takes them out (screener._book).
        "equity_includes_minority": bool(L.get("equity_from_total")) and (L.get("minority_interest") or 0) > 0,
        # Claims on the business besides debt, which come off the value of the whole business before it is split among
        # the common shares (report.other_claims): minority holders' stakes in subsidiaries, and preferred stock (at its
        # liquidation preference where the carrying amount is only nominal, as for banks; fundamentals._bank_parts).
        # Preferred stock above the company's total assets is a misread figure and counts as none (Emerson Radio's
        # liquidation preference tagged as $3.68B at June 2026, against $18M of assets and no preferred on its balance
        # sheet; an S-1's $329M at a company with $51M of assets).
        "minority_interest": L.get("minority_interest") or 0,
        "preferred_stock": _preferred_claim(L),
        "lt_debt_to_equity": (lt_debt / equity) if equity and equity > 0 and debt_known else None,
        "current_ratio": (L["current_assets"] / L["current_liabilities"]) if L.get("current_assets") and L.get("current_liabilities") else None,
        "roe": (ni / equity) if equity and equity > 0 else None,
        # The same measures without one-time items (equal to the above when there are none).
        "adj_net_income": adj_ni,
        "adj_net_margin": (adj_ni / rev) if rev and adj_ni is not None else None,
        "adj_roe": (adj_ni / equity) if equity and equity > 0 and adj_ni is not None else None,
        "one_time": one_time,
        "pretax_income": last.get("pretax"),
        "income_tax": last.get("income_tax"),
        # The rate the newest year's profit is normally taxed at, before any allowance for credits it earns every
        # year or a usual gap from it, and where it comes from: "own", "statutory" or "reit" (see _normal_rate).
        "normal_tax_rate": tax_normal,
        "tax_basis": tax_basis,
        "shares_out": L.get("shares_out"),
        # The cover page count's date, and the newest year's weighted average diluted share count, which build.py
        # checks the market value's share count against after a stock split.
        "shares_as_of": L.get("shares_as_of"),
        "diluted_shares": last.get("diluted_shares"),
        "total_assets": L.get("total_assets"),
        "profitable_years": sum(1 for x in recent3 if x > 0),
        "profitable_years_reported": sum(1 for s in ss[-3:] if (s.get("net_income") or 0) > 0),
        "years_checked": len(recent3),
        "revenue_growth": growth_1y,
        "revenue_cagr": cagr,
        "revenue_up_years": sum(1 for a, b in pairs if b > a),
        # Years in the back-to-back comparison, so revenue_years - 1 is the number of comparisons.
        "revenue_years": len(pairs) + 1 if revs else 0,
        "dilution": dil,
        "history": hist,
        # The newest fiscal year's gross profit and its share of revenue (_gross_profit), and where it comes from: the
        # company's gross profit line ("GrossProfit") or its revenue less the cost tag named. None for financial
        # companies and REITs, and where it can't be read soundly.
        "gross_profit": last.get("gross_profit") if gross else None,
        "gross_margin": last["gross_profit"] / rev if gross and last.get("gross_profit") is not None and rev and rev > 0
        else None,
        "gross_basis": last.get("gross_basis") if gross else None,
        # Why there is no gross profit, where there is none: _gross_profit's reason, or "bank", "reit" or "financial"
        # for the companies it doesn't apply to.
        "gross_why": (last.get("gross_why") or ("unknown" if last.get("gross_profit") is None else None)) if gross
        else "bank" if bank else "reit" if reit else "financial",
        # Up to QUARTERS_SHOWN quarters of revenue and gross profit, oldest first, and the newest four quarters'
        # together (_quarter_series): the trailing twelve months' revenue, gross profit and gross margin, and the day
        # they end, None where four quarters in a row can't be read on one footing, and the revenue of the four quarters
        # a year before them, where each of the four has its growth on the year before.
        "quarters": quarters,
        "ttm_revenue": ttm["revenue"] if ttm else None,
        "ttm_prior_revenue": ttm["prior_revenue"] if ttm else None,
        "ttm_gross_profit": ttm["gross_profit"] if ttm else None,
        "ttm_gross_margin": ttm["gross_profit"] / ttm["revenue"] if ttm and ttm["gross_profit"] is not None
        and ttm["revenue"] > 0 else None,
        "ttm_end": ttm["end"] if ttm else None,
        # Where the revenue of a company that tags no revenue line comes from, or None (see the docstring): a lender's
        # net interest income plus noninterest income ("bank"), a REIT's net interest income ("net_interest"), or a
        # business development company's total investment income ("investment_income").
        "revenue_basis": basis,
        # The newest year's net investment income, a business development company's earnings (DURATION's bdc_nii; None
        # where untagged), and the part of its net income that is not the common shareholders' (preferred dividends,
        # and minority holders' share where net income includes it: _common_gap), which report.py takes off the
        # earnings of a lender valued on its book value, as it does a bank's.
        "nii": last.get("nii"),
        "common_gap": last["common_gap"] if last.get("common_gap") is not None else last.get("preferred") or 0,
        # The newest year's preferred dividends as the income statement tags them (None where the frame failed).
        "preferred_dividends": last.get("preferred"),
        # The newest year's net income less the net income left to common shareholders as the company tags them, however
        # large (_reported_gap), and whether that net income is the whole business's, minority holders' share included
        # (ProfitLoss), rather than the company's own, whose gap to the common shareholders' is then preferred dividends.
        "common_gap_reported": _reported_gap(ni, last.get("ni_common")),
        "ni_whole": bool(last.get("ni_whole")),
        "loc": f.get("loc"),
        **(_reit_fields(ffo, window, series, rev, equity, L) if reit else {"reit": False}),
        **(_bank_fields(last, prev, adj_ni, one_time, L) if bank else {"bank": False}),
    }


def _reported_gap(ni, common):
    """Net income `ni` less the net income left to common shareholders as tagged (`common`), or None without both or where
    the tagged figure is a scale slip (a thousand or a million times off) or the gap is larger than either. Unlike
    _common_gap it isn't capped at COMMON_GAP_MAX of net income or sales, since preferred dividends can take far more
    of a lender's earnings: Chimera Investment's 2025 net income of $230.5M left $144.5M to its common shareholders, and
    NexPoint Real Estate Finance's $123.1M, with its operating partnership's other unitholders' share, left $75.7M."""
    if ni is None or common is None or not ni:
        return None
    if any(abs(common * k - ni) <= 0.02 * abs(ni) for k in (1e3, 1e6)):
        return None
    gap = ni - common
    return gap if abs(gap) <= max(abs(ni), abs(common)) else None


def investment_income(f):
    """Whether any fiscal year of a company's (a fundamentals record) takes its revenue from total investment income
    (INVESTMENT_INCOME), as a business development company's does."""
    return any(s.get("revenue_tag") == INVESTMENT_INCOME for s in ((f or {}).get("annual") or {}).values())


def pending(f):
    """What a company's filings support where derive() finds no fiscal year with both revenue and net income (a company
    that has filed quarterly reports but no annual report yet, as after a listing or a spin-off): its quarters of
    revenue and its latest balance sheet, {"quarters", "balance_as_of", "cash", "total_debt", "equity", "shares_out",
    "shares_as_of"}, or None where no quarter has revenue. No annual figure is ever worked out from the quarters.

    The quarters are those whose revenue the frames give under one tag, the first (DURATION) the newest such quarter
    uses, as _quarter_series lists them ({"start", "end", "fiscal_quarter": None, "revenue", "gross_profit": None,
    "derived": False, "yoy", "extra_days", "yoy_withheld"}, oldest first, back to back with blank quarters in whole-
    quarter gaps), with growth on the quarter a year before where both come from one filing (a 10-Q gives both) or from
    filings on one footing (_footings). A figure from a filing other than the company's own 10-Qs and 10-Ks gives way to
    the latest one they give for the period, or is left out; a quarter more than QUARTER_SCALE from the middle of the
    others, or overlapping another, is left out, as the fiscal year check in _quarter_series can't be made."""
    D, day = dt.date.fromisoformat, dt.timedelta(days=1)
    raw, L = f.get("quarters") or {}, f.get("latest") or {}
    concept, unmatched = f.get("concept") or {}, f.get("unmatched") or set()
    spans = sorted((D(a), D(b)) for (a, b), facts in raw.items()
                   if QUARTER_DAYS[0] <= (D(b) - D(a)).days + 1 <= QUARTER_DAYS[1] and REVENUE_TAGS & facts.keys())
    out = []
    if spans:
        newest = raw[tuple(x.isoformat() for x in spans[-1])]
        tag = next(t for t in REVENUE_ORDER if t in newest)
        rows = {t: rs for t, rs in concept.items() if t == tag}
        footing = _footings(rows) if rows else {}
        periods = []
        for a, b in spans:
            d = raw[(a.isoformat(), b.isoformat())].get(tag)
            if d is None:
                continue
            val, accn = d["val"], d.get("accn")
            if accn in unmatched:
                row = next((r for r in reversed(rows.get(tag) or []) if (r[0], r[1]) == (a.isoformat(), b.isoformat())),
                           None)
                if row is None:
                    continue
                val, accn = row[2], row[3]
            periods.append({"start": a, "end": b, "revenue": val, "accn": accn})
        # Two readings of one quarter can't be told apart, so neither counts.
        drop = set()
        for i, (p, q) in enumerate(zip(periods, periods[1:])):
            if q["start"] <= p["end"] - SEAM_DAYS * day:
                drop |= {i, i + 1}
        periods = [p for i, p in enumerate(periods) if i not in drop]
        mid = statistics.median(p["revenue"] for p in periods) if periods else 0
        if mid > 0:
            periods = [p for p in periods if 1 / QUARTER_SCALE <= p["revenue"] / mid <= QUARTER_SCALE]
        given = {(footing.get(r[3]), r[0], r[1]): r[2] for r in rows.get(tag) or []}

        def same_footing(x, y):
            if x["accn"] and x["accn"] == y["accn"]:
                return True
            return any(all(abs(given.get((fo, p["start"].isoformat(), p["end"].isoformat()), math.inf) - p["revenue"])
                           <= RESTATED_GAP * abs(p["revenue"]) for p in (x, y)) for fo in set(footing.values()))

        slots = []
        for p in periods[-(QUARTERS_SHOWN + 4):]:
            if slots:
                gap = (p["start"] - slots[-1]["end"]).days - 1
                n = round(gap / QUARTER_MEAN) if gap > SEAM_DAYS else 0
                if gap > SEAM_DAYS and not (n == 1 and QUARTER_DAYS[0] <= gap <= QUARTER_DAYS[1]
                                            or n > 1 and abs(gap - n * QUARTER_MEAN) <= BLANK_SLACK):
                    slots = []
                elif gap > SEAM_DAYS:
                    first = slots[-1]["end"] + day
                    for k in range(1, n + 1):
                        b = p["start"] - day if k == n else first + dt.timedelta(days=round(k * (gap + 1) / n)) - day
                        slots.append({"start": slots[-1]["end"] + day, "end": b, "revenue": None, "accn": None,
                                      "estimated": n > 1})
            slots.append(p)
        for s in slots:
            ago = next((x for x in slots if abs((s["end"] - x["end"]).days - 365) <= YEAR_AGO_DAYS), None)
            yoy = extra = withheld = None
            if ago is not None and s["revenue"] is not None and ago["revenue"] is not None:
                if ago["revenue"] <= 0:
                    withheld = "no_base"
                elif same_footing(s, ago):
                    yoy = s["revenue"] / ago["revenue"] - 1
                    extra = (s["end"] - s["start"]).days - (ago["end"] - ago["start"]).days
                else:
                    withheld = "unchecked"
            out.append({"start": s["start"].isoformat(), "end": s["end"].isoformat(), "fiscal_quarter": None,
                        "revenue": s["revenue"], "gross_profit": None, "derived": False, "yoy": yoy,
                        "extra_days": extra if extra is not None and abs(extra) >= EXTRA_DAYS else None,
                        "yoy_withheld": withheld, **({"dates_estimated": True} if s.get("estimated") else {})})
        out = out[-QUARTERS_SHOWN:]
        while out and out[0]["revenue"] is None:
            out.pop(0)
    if not out:
        return None
    return {"quarters": out, "balance_as_of": L.get("as_of"),
            "cash": (L.get("cash") or 0) + (L.get("st_investments") or 0) + (L.get("lt_securities") or 0)
            if L.get("as_of") else None,
            "total_debt": L.get("total_debt") if L.get("as_of") else None, "equity": L.get("equity"),
            "shares_out": L.get("shares_out"), "shares_as_of": L.get("shares_as_of")}


def _bank_fees(s):
    """A bank's year `s` (derive's) with the noninterest income its pretax income implies, where it tags none, and its
    revenue from that (_bank_year)."""
    nii, provision = s.get("bank_nii"), s.get("bank_provision")
    costs, pretax = s.get("bank_noninterest_expense"), s.get("pretax")
    if s.get("bank_noninterest_income") is not None or None in (nii, provision, costs, pretax):
        return s
    fees = pretax - (nii - provision) + costs
    return dict(s, bank_noninterest_income=fees, bank_revenue=nii + fees, revenue=nii + fees)


def _core_revenue(revenue, items, sales=False):
    """A bank's revenue without the one-time gains and losses on investment securities among `items` (_adjust), and
    with `sales` those on selling a business or assets too."""
    if revenue is None:
        return None
    kinds = ("securities_gain", "securities_loss") + (("sale_gain", "sale_loss") if sales else ())
    for i in items or []:
        if i["kind"] in kinds:
            revenue -= i["amount"] if i["kind"].endswith("_gain") else -i["amount"]
    return revenue


# How far from a bank's fiscal year end a quarter's balance sheet may be dated and still count as the year's end
# (_bank_fields), since a few banks close their books on the last business day or a Saturday.
YEAR_END_DAYS = 10
# The longest after a bank's fiscal year end that its latest balance sheet may be dated for its returns to be measured
# on it, when no balance sheet at the year end is in the frames (_bank_fields).
RETURNS_MAX_DAYS = 400
# The highest dividend rate on preferred stock among the 43 banks whose 2025 preferred dividends and tagged preferred
# stock give a plausible one, 1% to 20% (quartiles 4.9%, 6.0% and 7.0%; the highest 8.6%), so dividends over it give
# the least preferred stock a bank can have; and the share of tangible book value that least amount may reach, unread,
# before tangible book value is taken as unknown (_bank_fields).
PREFERRED_RATE_MAX = 0.086
PREFERRED_MIN = 0.03


def _tangible_equity(q, year_end=None, preferred_paid=False):
    """(tangible common equity, goodwill and other intangible assets) from one quarter's parts `q` (_bank_parts), or
    Nones without them. Tangible common equity is shareholders' equity less preferred stock, goodwill and other
    intangible assets: what common shareholders would keep if the premiums paid for acquisitions were worth nothing,
    the book value banks and their analysts value a bank on (without the deferred taxes on goodwill and intangibles that
    some banks add back, which the frames don't give). A line untagged in `q` is taken from the fiscal year end's
    balance sheet `year_end`, since quarterly reports tag fewer lines than the annual report (Bank of America tagged its
    $26.0B of preferred stock at its 2025 year end and in none of its 2026 quarterly reports), preferred stock only
    when the year's income statement shows it was paid dividends (`preferred_paid`), since a bank that redeemed it
    tags none afterwards. Otherwise an untagged line counts as none."""
    if not q:
        return None, None

    def line(k):
        v = q[k]
        if v is None and year_end and (k != "preferred" or preferred_paid):
            v = year_end[k]
        return v or 0

    other = line("other")
    if q["other"] is None and q.get("combined") and year_end and year_end["other"] and year_end["goodwill"] is not None:
        # A quarter that tags only goodwill and other intangible assets together (_bank_parts) has them both in that
        # figure when it is nearer the year end's goodwill plus other intangible assets than its goodwill alone
        # (Eastern Bankshares' June 2026 $1.28B, against $1.12B and $184M at the 2025 year end), else goodwill alone
        # (CNB Financial's $87.5M, against $88.4M and $33.7M).
        g, yg, yo = q["goodwill"] or 0, year_end["goodwill"], year_end["other"]
        if abs(g - yg - yo) < abs(g - yg):
            other = 0.0
    if q["lumped"] is None and year_end and year_end["lumped"] and q["other"] is not None:
        # A bank whose annual report showed its servicing rights inside its other intangible assets (_bank_parts)
        # still has them there in a quarter that tags only the total.
        other = max(other - q["servicing"], 0)
    intangible = line("goodwill") + other
    return q["equity"] - line("preferred") - intangible, intangible


def _bank_fields(last, prev, adj_ni, items, L):
    """derive()'s bank measures for the newest year `last` (its revenue without one-time securities gains and losses),
    whose net income without the one-time `items` is `adj_ni`, the year before it `prev` ({} without one), and the
    latest balance sheet L.

    Earnings are the common shareholders': net income less what goes to preferred shareholders (_common_gap), and
    per share over the year's diluted share count, which is how a price to earnings is quoted. Returns are measured
    against the balance sheet at the end of the fiscal year they were earned in, when a recent quarter end matches it
    (Fifth Third's equity grew from $21.7B to $34.5B by June 2026 as it bought Comerica, whose profit is in none of its
    2025 results), else the latest one if it is at most RETURNS_MAX_DAYS later, and they are unknown when the year end's
    balance sheet frames failed. Return on assets and on tangible common equity (ROTCE) and the efficiency ratio
    (noninterest expense per dollar of revenue, lower is better) are the measures bank analysts rank banks by; tangible
    common equity as a share of tangible assets is the capital cushion that absorbs loan losses. The efficiency
    ratio leaves out one-time write-downs, which a bank books among its noninterest expenses, as banks show it (Midland
    States' 2025 costs came to 117% of its revenue with them)."""
    gap = last.get("common_gap")
    common = adj_ni - (gap if gap is not None else last.get("preferred") or 0) if adj_ni is not None else None
    quarters = L.get("quarters") or {}
    end, at, unread = last.get("end"), None, None
    if end:
        near = [(abs((dt.date.fromisoformat(d) - dt.date.fromisoformat(end)).days), d) for d in quarters]
        near = [x for x in near if x[0] <= YEAR_END_DAYS]
        at = quarters[min(near)[1]] if near else None
        if near and at is None:
            # Its year-end lines, which the latest quarter's untagged ones are taken from, are unknown (a frame failed).
            unread = "year_end"
    paid = max(last.get("preferred") or 0, gap or 0)
    latest = quarters.get(L.get("as_of"))
    tce, intangible = _tangible_equity(latest, at, paid > 0)
    # From the equity statement's tags or the income statement's (preferred, also read for REITs).
    dividends = max(last.get("bank_preferred_dividends") or 0, last.get("preferred") or 0)
    if tce is not None and dividends > 0:
        # Preferred dividends paid (bank_preferred_dividends) but no preferred stock read: untagged at the latest
        # quarter and the year end (State Street's $3.56B, WesBanco's $230M), or tagged at nothing in every recent
        # quarter, which some banks do for its nominal par value (PNC's). Tangible book value is unknown where the
        # preferred stock the dividends imply, at the highest rate PREFERRED_RATE_MAX, is over PREFERRED_MIN of it. A bank
        # that redeemed it tags none afterwards, having tagged it before (Customers Bancorp's $137.9M in June 2025,
        # nothing at the 2025 year end).
        pref = latest["preferred"] if latest["preferred"] is not None else (at or {}).get("preferred")
        if pref is None or not pref and not any((x or {}).get("preferred") for x in quarters.values()):
            if dividends / PREFERRED_RATE_MAX > PREFERRED_MIN * max(tce, 0):
                unread = unread or "preferred"
    base = {} if unread == "year_end" else at or latest or {}
    if at is None and end and L.get("as_of") and (
            dt.date.fromisoformat(L["as_of"]) - dt.date.fromisoformat(end)).days > RETURNS_MAX_DAYS:
        base = {}  # a balance sheet this long after the year describes a different bank than the one that earned it
    base_tce, base_intangible = _tangible_equity(base or None, at, paid > 0)
    if unread:
        tce = base_tce = None
    ret = lambda x, b: x / b if x is not None and b and b > 0 else None
    rev, costs = last.get("revenue"), last.get("bank_noninterest_expense")
    down = sum(i["amount"] for i in items or [] if i["kind"] in ("goodwill_impairment", "impairment"))
    shares = last.get("diluted_shares")
    tangible_assets = latest["assets"] - intangible if latest and intangible is not None else None
    return {
        "bank": True,
        "net_interest_income": last.get("bank_nii"),
        "noninterest_income": last.get("bank_noninterest_income"),
        "noninterest_expense": costs,
        "provision": last.get("bank_provision"),
        # The year before's provision, and the goodwill the year's acquisitions added (report._provision_spike).
        "provision_prior": prev.get("bank_provision"),
        "goodwill_acquired": last.get("bank_goodwill_acquired"),
        # Revenue without one-time securities gains and losses, which growth and efficiency are measured on (derive).
        "adj_revenue": rev,
        # A write-down larger than the costs it would come out of was not among them.
        "efficiency_ratio": (costs - (down if down < costs else 0)) / rev if costs is not None and rev and rev > 0
        else None,
        # Net income without one-time items left to common shareholders, and per diluted share (over diluted_shares,
        # which report.py checks against the share count behind the price before quoting a price to earnings on it).
        "adj_net_income_common": common,
        "adj_eps": common / shares if common is not None and shares and shares > 0 else None,
        "diluted_shares": shares,
        # Common shares outstanding on the latest balance sheet's date, where tagged (BANK_SHARES).
        "bank_shares": (latest or {}).get("shares"),
        # The balance sheet the returns are measured on: the fiscal year end, or the latest when none matches it and it
        # is at most RETURNS_MAX_DAYS later.
        "returns_as_of": end if at else L.get("as_of") if latest and base else None,
        "adj_roa": ret(adj_ni, base.get("assets")),
        "adj_roe_common": ret(common, None if base_tce is None else base_tce + base_intangible),
        "adj_rotce": ret(common, base_tce),
        # Tangible common equity on the latest balance sheet, which the price to tangible book value is measured on, and
        # its share of tangible assets. None, as is the return on common equity, when a line it needs is unknown
        # ("tce_unread": "preferred" for preferred stock the filings don't give, "year_end" where the year end's
        # balance sheet frames failed).
        "tangible_equity": tce,
        "tce_ratio": tce / tangible_assets if tce is not None and tangible_assets and tangible_assets > 0 else None,
        "tce_unread": unread,
    }


def _reit_fields(ffo, window, series, rev, equity, L):
    """derive()'s REIT measures, from _ffo(): the newest year's funds from operations (as the filings give it, and
    without one-time items, which the valuation and scores use), its parts, the same as a share of sales and of the
    shareholders' equity, in how many of the last three years it was positive, and what report.reit_kind() tells a
    landlord from a lender by.

    Where holders of an operating partnership's units own part of the business (_owners), FFO is the listed shares'
    part of it, so its margin and return on equity are measured on the whole business's FFO, against the whole
    business's sales and equity (Strawberry Fields' Class A shares own 23% of it: its whole FFO came to 51% of its
    sales)."""
    ffo_now, adj, parts, own = ffo[window[-1]]
    own = own if own > 0 else 1.0
    recent = [ffo[j][1] for j in window[-3:] if ffo[j][1] is not None]
    whole_equity = L.get("equity_total") if own < 0.995 else equity
    assets = L.get("total_assets")
    newest = series[window[-1]]
    return {
        "reit": True,
        "ffo": ffo_now,
        "adj_ffo": adj,
        "ffo_parts": parts,
        "adj_ffo_margin": adj / own / rev if adj is not None and rev else None,
        "adj_ffo_roe": adj / own / whole_equity if adj is not None and whole_equity and whole_equity > 0 else None,
        # The equity it is measured on as a share of total assets, where it may be too worn down by years of
        # depreciation and payouts to mean anything (report._ffo_profit_bars).
        "ffo_equity_to_assets": whole_equity / assets if whole_equity is not None and assets and assets > 0 else None,
        "ffo_positive_years": sum(1 for x in recent if x > 0),
        "ffo_years_checked": len(recent),
        # Depreciation (all of it, before any minority share) as a share of sales and of total assets, and rent as a
        # share of total assets, which set a REIT that owns property apart from one that lends or whose leases are
        # booked as loans (report.reit_kind).
        "depreciation_share": parts["depreciation"] / own / rev if parts and rev and rev > 0 else None,
        "depreciation_to_assets": parts["depreciation"] / own / assets if parts and assets and assets > 0 else None,
        "rent_to_assets": (newest.get("lease_income") or 0) / assets if assets and assets > 0 else None,
        "depreciation_years": sum(1 for j in window if series[j].get("depreciation") is not None),
        # Years whose depreciation frames failed, so a missing figure there is unknown rather than untagged.
        "depreciation_failed": sum(1 for j in window if series[j].get("depreciation_failed")),
        # The listed shares' part of the whole business where an operating partnership's unitholders own the rest
        # (_owners), which a market value counting only the listed shares must be set against (report.peer_table).
        "owners_share": own if own < 0.995 else 1.0,
    }


# The least share of total liabilities a REIT's debt is taken to be when read (_reit_debt). On September 2026 data,
# the 131 REITs that own property whose debt it read had debt of 56% to 100% of their liabilities (a median 89%): the
# rest is mostly rent paid in advance, leases of the land under their buildings and bills.
REIT_DEBT_MIN = 0.5


def _reit_debt(L):
    """(total debt, whether it was read) for a REIT, from its latest balance sheet: the largest of _debt()'s reading
    and those of the debt tags many REITs use instead (the carrying amount of all its debt instruments, notes and
    loans payable, bank loans, unsecured and secured debt with the credit line, debt with finance leases), leaving out
    any reading above its total liabilities, which must be a double count (Saul Centers' 2026: $2.83B against $1.69B of
    liabilities). Debt still under REIT_DEBT_MIN of liabilities that are more than a tenth of assets was not read
    (Hudson Pacific tags its debt only under its own names), so its net cash is unknown, and so is no debt at all in a
    run whose liabilities frames failed."""
    tags, liab, assets = L.get("reit_debt") or {}, L.get("liabilities"), L.get("total_assets") or 0
    g = lambda t: tags.get(t) or 0
    readings = [L.get("total_debt") or 0, g("DebtInstrumentCarryingAmount"), g("NotesAndLoansPayable"),
                g("UnsecuredDebt") + g("SecuredDebt") + g("LineOfCredit") + g("NotesPayableToBank"),
                g("DebtAndCapitalLeaseObligations")]
    if liab:
        readings = [r for r in readings if r <= liab * 1.001] or [0]
    debt = max(readings)
    known = debt > 0 if not liab else debt >= REIT_DEBT_MIN * liab or liab <= 0.1 * assets
    return debt, known


# The debt check (_debt_check). On September 2026 data, the 1,428 companies outside finance with $50M or more of debt
# read paid a median 5.1% of it in interest in their latest year, 13% at the 95th percentile. More than DEBT_RATE_MAX
# is taken to mean debt the reader missed, when the interest comes to at least DEBT_INTEREST_MIN of total assets, so
# that the missing debt would matter (at 5% interest, a tenth of the assets or more). That caught T-Mobile ($3.9B of
# interest against the $6.1B of debt read), General Motors and Caterpillar (no debt read, beside finance arms that
# borrow tens of billions) and Deere ($3.1B against $17.1B). Ford tags neither its debt nor its interest paid outside
# its segments, so a second test reads the balance sheet itself: noncurrent liabilities other than operating leases
# (most of a store chain's) of at least DEBT_LIAB_SHARE of total assets and more than DEBT_LIAB_TIMES the debt read
# ($140B at Ford, which read none).
DEBT_RATE_MAX = 0.15
DEBT_INTEREST_MIN = 0.005
DEBT_LIAB_SHARE = 0.4
DEBT_LIAB_TIMES = 10


def _debt_check(L, interest):
    """Why the debt _debt() read from the latest balance sheet `L` looks far too small to be the company's whole debt,
    or None: "interest" where the interest it paid in its newest year (`interest`, None where unknown) is more than
    DEBT_RATE_MAX of the most debt read at a recent quarter end, "liabilities" where its noncurrent liabilities are
    mostly something other than the debt read. See the notes above DEBT_RATE_MAX."""
    assets = L.get("total_assets")
    if not assets or assets <= 0:
        return None
    debt = L.get("total_debt") or 0
    if interest and interest > DEBT_RATE_MAX * max(debt, L.get("peak_debt") or 0) \
            and interest >= DEBT_INTEREST_MIN * assets:
        return "interest"
    equity = L["equity_total"] if L.get("equity_total") is not None else L.get("equity")
    liab = L["liabilities"] if L.get("liabilities") is not None else assets - equity if equity is not None else None
    cl, lease = L.get("current_liabilities"), L.get("lease_nc")
    if liab is not None and cl is not None and lease is not None:
        other = liab - cl - lease
        if other >= DEBT_LIAB_SHARE * assets and other > DEBT_LIAB_TIMES * debt:
            return "liabilities"
    return None
