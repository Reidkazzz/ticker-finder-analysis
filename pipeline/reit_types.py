"""Property types of REITs, from their annual reports, keyed by SEC CIK, which a ticker change leaves alone.

REITs are compared with REITs of the same property type first (report.peers_for, REIT_TYPE_GAP), as analysts compare
them, since types trade at lasting different multiples of their funds from operations: in September 2026 office REITs
at a median 8.5x, data centers and towers at 22.9x. The groups follow Nareit's property sectors, some merged
where there are too few REITs to compare. Kept by hand: a REIT missing from it is compared with all REITs that own
property, and a timber REIT is not valued on funds from operations (report.reit_kind)."""

TYPES = {
    # office: office buildings
    1035443: "office",  # ARE Alexandria Real Estate Equities Inc.
    790816: "office",  # BDN Brandywine Realty Trust
    1037540: "office",  # BXP BXP Inc.
    860546: "office",  # CDP COPT Defense Properties
    25232: "office",  # CUZ Cousins Properties Incorporated
    1622194: "office",  # DEA Easterly Government Properties Inc.
    1364250: "office",  # DEI Douglas Emmett Inc.
    1541401: "office",  # ESRT Empire State Realty Trust Inc.
    1031316: "office",  # FSP Franklin Street Properties Corp.
    921082: "office",  # HIW Highwoods Properties Inc.
    1482512: "office",  # HPP Hudson Pacific Properties Inc.
    1689796: "office",  # JBGS JBG SMITH Properties
    1025996: "office",  # KRC Kilroy Realty Corporation
    1952976: "office",  # NLOP Net Lease Office Properties
    1595527: "office",  # NYC American Strategic Investment Co.
    1873923: "office",  # ONL Orion Properties Inc.
    1456772: "office",  # OPI Office Properties Income Trust
    1042776: "office",  # PDM Piedmont Realty Trust Inc.
    1040971: "office",  # SLG SL Green Realty Corp
    899689: "office",  # VNO Vornado Realty Trust
    # industrial: warehouses, logistics and cold storage (and cannabis growing sites, which Nareit counts as industrial)
    1455863: "industrial",  # COLD Americold Realty Trust Inc.
    49600: "industrial",  # EGP EastGroup Properties Inc.
    921825: "industrial",  # FR First Industrial Realty Trust Inc.
    1677576: "industrial",  # IIPR Innovative Industrial Properties Inc.
    1717307: "industrial",  # ILPT Industrial Logistics Properties Trust
    1868159: "industrial",  # LINE Lineage Inc.
    910108: "industrial",  # LXP LXP Industrial Trust
    712770: "industrial",  # OLP One Liberty Properties Inc.
    1045609: "industrial",  # PLD Prologis Inc.
    1571283: "industrial",  # REXR Rexford Industrial Realty Inc.
    1479094: "industrial",  # STAG Stag Industrial Inc.
    1476150: "industrial",  # TRNO Terreno Realty Corporation
    # retail: shopping centers and malls
    899629: "retail",  # AKR Acadia Realty Trust
    907254: "retail",  # BFS Saul Centers Inc.
    1581068: "retail",  # BRX Brixmor Property Group Inc.
    910612: "retail",  # CBL CBL & Associates Properties Inc.
    23795: "retail",  # CTO CTO Realty Growth Inc.
    2027317: "retail",  # CURB Curbline Properties Corp.
    34903: "retail",  # FRT Federal Realty Investment Trust
    1307748: "retail",  # IVT InvenTrust Properties Corp.
    879101: "retail",  # KIM Kimco Realty Corporation (HC)
    1286043: "retail",  # KRG Kite Realty Group Trust
    912242: "retail",  # MAC Macerich Company (The)
    1476204: "retail",  # PECO Phillips Edison & Company Inc.
    910606: "retail",  # REG Regency Centers Corporation
    894315: "retail",  # SITC SITE Centers Corp.
    1063761: "retail",  # SPG Simon Property Group Inc.
    1611547: "retail",  # UE Urban Edge Properties
    1527541: "retail",  # WHLR Wheeler Real Estate Investment Trust Inc.
    # net_lease: single-tenant properties leased long term, the tenant paying the property costs
    917251: "net_lease",  # ADC Agree Realty Corporation
    1424182: "net_lease",  # BNL Broadstone Net Lease Inc.
    1728951: "net_lease",  # EPRT Essential Properties Realty Trust Inc.
    1650132: "net_lease",  # FCPT Four Corners Property Trust Inc.
    1988494: "net_lease",  # FVR FrontView REIT Inc.
    1651721: "net_lease",  # GIPR Generation Income Properties Inc.
    1526113: "net_lease",  # GNL Global Net Lease Inc.
    1234006: "net_lease",  # GOOD Gladstone Commercial Corporation Real Estate Investment Trus
    1052752: "net_lease",  # GTY Getty Realty Corporation
    751364: "net_lease",  # NNN NNN REIT Inc.
    1798100: "net_lease",  # NTST NetSTREIT Corp.
    726728: "net_lease",  # O Realty Income Corporation
    1786117: "net_lease",  # PINE Alpine Income Property Trust Inc.
    1759774: "net_lease",  # PSTL Postal Realty Trust Inc.
    1025378: "net_lease",  # WPC W. P. Carey Inc. REIT
    # residential: apartments, rented houses and manufactured home communities
    922864: "residential",  # AIV Apartment Investment and Management Company
    1562401: "residential",  # AMH American Homes 4 Rent
    1903382: "residential",  # BHM Bluerock Homes Trust Inc.
    14846: "residential",  # BRT BRT Apartments Corp. (MD)
    1649096: "residential",  # CLPR Clipper Realty Inc.
    906345: "residential",  # CPT Camden Property Trust
    798359: "residential",  # CSR D/B/A Centerspace
    104894: "residential",  # ELME Elme Communities
    895417: "residential",  # ELS Equity Lifestyle Properties Inc.
    920522: "residential",  # ESS Essex Property Trust Inc.
    1687229: "residential",  # INVH Invitation Homes Inc.
    1466085: "residential",  # IRT Independence Realty Trust Inc.
    912595: "residential",  # MAA Mid-America Apartment Communities Inc.
    1620393: "residential",  # NXRT NexPoint Residential Trust Inc.
    912593: "residential",  # SUI Sun Communities Inc.
    74208: "residential",  # UDR UDR Inc.
    752642: "residential",  # UMH UMH Properties Inc.
    906107: "residential",  # VMRK Vivmark Residential
    # health: senior housing, medical offices, hospitals and nursing homes
    1632970: "health",  # AHR American Healthcare REIT Inc.
    1631569: "health",  # CHCT Community Healthcare Trust Incorporated
    1590717: "health",  # CTRE CareTrust REIT Inc.
    1075415: "health",  # DHC Diversified Healthcare Trust
    765880: "health",  # DOC Healthpeak Properties Inc.
    1360604: "health",  # HR Healthcare Realty Trust Incorporated
    2100805: "health",  # JAN Janus Living Inc. Class A-1
    887905: "health",  # LTC LTC Properties Inc.
    1287865: "health",  # MPT Medical Properties Trust Inc.
    877860: "health",  # NHI National Health Investors Inc.
    1561032: "health",  # NHP National Healthcare Properties Inc.
    888491: "health",  # OHI Omega Healthcare Investors Inc.
    1492298: "health",  # SBRA Sabra Health Care REIT Inc.
    1782430: "health",  # STRW Strawberry Fields REIT Inc.
    798783: "health",  # UHT Universal Health Realty Income Trust
    740260: "health",  # VTR Ventas Inc.
    766704: "health",  # WELL Welltower Inc.
    1533615: "health",  # XRN Chiron Real Estate Inc.
    # lodging: hotels
    1232582: "lodging",  # AHT Ashford Hospitality Trust Inc
    1418121: "lodging",  # APLE Apple Hospitality REIT Inc.
    1574085: "lodging",  # BHR Braemar Hotels & Resorts Inc.
    1476045: "lodging",  # CLDT Chatham Lodging Trust (REIT)
    1298946: "lodging",  # DRH Diamondrock Hospitality Company
    1070750: "lodging",  # HST Host Hotels & Resorts Inc.
    82473: "lodging",  # IHT InnSuites Hospitality Trust Shares of Beneficial Interest
    1497645: "lodging",  # INN Summit Hotel Properties Inc.
    1474098: "lodging",  # PEB Pebblebrook Hotel Trust
    1040829: "lodging",  # RHP Ryman Hospitality Properties Inc. (REIT)
    1511337: "lodging",  # RLJ RLJ Lodging Trust
    1295810: "lodging",  # SHO Sunstone Hotel Investors Inc. Sunstone Hotel Investors Inc.
    945394: "lodging",  # SVC Service Properties Trust
    1616000: "lodging",  # XHR Xenia Hotels & Resorts Inc.
    # storage: self storage
    1298675: "storage",  # CUBE CubeSmart
    1289490: "storage",  # EXR Extra Space Storage Inc
    1393311: "storage",  # PSA Public Storage
    1031235: "storage",  # SELF Global Self Storage Inc.
    1585389: "storage",  # SMA SmartStop Self Storage REIT Inc.
    # tech: data centers and cell towers (and Iron Mountain's records storage and data centers)
    1053507: "tech",  # AMT American Tower Corporation (REIT)
    1051470: "tech",  # CCI Crown Castle Inc.
    1297996: "tech",  # DLR Digital Realty Trust Inc.
    1101239: "tech",  # EQIX Equinix Inc.
    1020569: "tech",  # IRM Iron Mountain Incorporated (Delaware)Common Stock REIT
    1034054: "tech",  # SBAC SBA Communications Corporation
    # specialty: billboards, farmland, casinos, entertainment venues and ground leases
    1045450: "specialty",  # EPR EPR Properties
    1591670: "specialty",  # FPI Farmland Partners Inc.
    1575965: "specialty",  # GLPI Gaming and Leisure Properties Inc.
    1090425: "specialty",  # LAMR Lamar Advertising Company
    1495240: "specialty",  # LAND Gladstone Land Corporation
    1579877: "specialty",  # OUT OUTFRONT Media Inc.
    1095651: "specialty",  # SAFE Safehold Inc. New
    1705696: "specialty",  # VICI VICI Properties Inc.
    # timber: timberland
    52827: "timber",  # RYN Rayonier Inc. REIT
    106535: "timber",  # WY Weyerhaeuser Company
    # diversified: several kinds at once
    1500217: "diversified",  # AAT American Assets Trust Inc.
    1569187: "diversified",  # AHRT AH Realty Trust Inc.
    3499: "diversified",  # ALX Alexander's Inc.
    908311: "diversified",  # CMCT Creative Media & Community Trust Corporation
    1654595: "diversified",  # MDRR Medalist Diversified Inc.
    1550913: "diversified",  # MKZR MacKenzie Realty Capital Inc.
    1356115: "diversified",  # NXDT NexPoint Diversified Real Estate Trust
    1080657: "diversified",  # SQFT Presidio Property Trust Inc.
}
