#!/usr/bin/env python3
"""A one-time purchase, the same in both stores: its texts and, if given, its price.

    compra.py <package> <Play product> <App Store product> [<price in Spain>] [--dry] [--solo-play]

Texts: the App Store ones (name and description in every language) are copied to the Play product,
so the purchase reads the same in both stores. App Store Connect is the source because its purchase
is the one App Review reads.

Price: in the App Store, Spain becomes the base territory at that price (VAT included) and Apple
equalizes the other territories. In Play, every region where Play charges the same currency as Apple
gets Apple's price; the rest (Play charges local currency where Apple charges dollars) get Play's own
conversion of the same price before tax. A buyer sees the same number in both stores wherever that
can be.

The bundle id is the package name, as in the whole family. `--dry` prints and changes nothing;
`--solo-play` leaves the App Store as it is (a purchase in review, whose notes quote its price).
"""
import pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import appstore, play  # noqa: E402

# Apple names territories in ISO 3166 alpha-3, Play in alpha-2.
A2 = dict(x.split(":") for x in """
AFG:AF AGO:AO AIA:AI ALB:AL ARE:AE ARG:AR ARM:AM ATG:AG AUS:AU AUT:AT AZE:AZ BEL:BE BEN:BJ BFA:BF
BGR:BG BHR:BH BHS:BS BIH:BA BLR:BY BLZ:BZ BMU:BM BOL:BO BRA:BR BRB:BB BRN:BN BTN:BT BWA:BW CAN:CA
CHE:CH CHL:CL CHN:CN CIV:CI CMR:CM COD:CD COG:CG COL:CO CPV:CV CRI:CR CYM:KY CYP:CY CZE:CZ DEU:DE
DMA:DM DNK:DK DOM:DO DZA:DZ ECU:EC EGY:EG ESP:ES EST:EE FIN:FI FJI:FJ FRA:FR FSM:FM GAB:GA GBR:GB
GEO:GE GHA:GH GMB:GM GNB:GW GRC:GR GRD:GD GTM:GT GUY:GY HKG:HK HND:HN HRV:HR HUN:HU IDN:ID IND:IN
IRL:IE IRQ:IQ ISL:IS ISR:IL ITA:IT JAM:JM JOR:JO JPN:JP KAZ:KZ KEN:KE KGZ:KG KHM:KH KNA:KN KOR:KR
KWT:KW LAO:LA LBN:LB LBR:LR LBY:LY LCA:LC LKA:LK LTU:LT LUX:LU LVA:LV MAC:MO MAR:MA MDA:MD MDG:MG
MDV:MV MEX:MX MKD:MK MLI:ML MLT:MT MMR:MM MNE:ME MNG:MN MOZ:MZ MRT:MR MSR:MS MUS:MU MWI:MW MYS:MY
NAM:NA NER:NE NGA:NG NIC:NI NLD:NL NOR:NO NPL:NP NRU:NR NZL:NZ OMN:OM PAK:PK PAN:PA PER:PE PHL:PH
PLW:PW PNG:PG POL:PL PRT:PT PRY:PY QAT:QA ROU:RO RUS:RU RWA:RW SAU:SA SEN:SN SGP:SG SLB:SB SLE:SL
SLV:SV SRB:RS STP:ST SUR:SR SVK:SK SVN:SI SWE:SE SWZ:SZ SYC:SC TCA:TC TCD:TD THA:TH TJK:TJ TKM:TM
TON:TO TTO:TT TUN:TN TUR:TR TWN:TW TZA:TZ UGA:UG UKR:UA URY:UY USA:US UZB:UZ VCT:VC VEN:VE VGB:VG
VNM:VN VUT:VU XKS:XK YEM:YE ZAF:ZA ZMB:ZM ZWE:ZW""".split())


def show(m):
    return f"{m['units']}.{m['nanos'] // 10**7:02d} {m['currencyCode']}"


# Apple drops the region where it has one language per country; Play always keeps it.
PLAY_LOCALE = {"hi": "hi-IN", "it": "it-IT", "ja": "ja-JP", "ko": "ko-KR", "pl": "pl-PL", "ru": "ru-RU", "tr": "tr-TR"}


def main(package, play_product, asc_product, price, dry, only_play):
    purchase = appstore.purchase(package, asc_product)
    texts = {PLAY_LOCALE.get(l, l): t for l, t in appstore.purchase_texts(purchase).items()}
    if not price:
        play.compra(package, play_product, None, texts, dry)
        return
    point = appstore.price_point(purchase, price)
    apple = appstore.equalized(point)
    apple["ESP"] = (price, "EUR")
    prices = play.convert(package, play.money(float(price) / 1.21, "EUR"))
    same = 0
    for t, (amount, currency) in apple.items():
        r = A2.get(t)
        if r in prices and prices[r]["currencyCode"] == currency:
            prices[r] = play.money(amount, currency)
            same += 1
    print(f"{package}: {price} EUR en España; {same} de {len(prices)} regiones de Play con el precio de Apple")
    for t in ("ESP", "DEU", "USA", "GBR", "JPN", "IND", "BRA", "MEX"):
        print(f"  {t}  App Store {apple[t][0]} {apple[t][1]:<4} Play {show(prices[A2[t]])}")
    play.compra(package, play_product, prices, texts, dry)
    if not dry and not only_play:
        appstore.set_price(purchase, point)
        print(f"  {asc_product}: precio guardado en App Store")


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if not x.startswith("--")]
    if len(a) not in (3, 4):
        sys.exit(__doc__)
    main(*a[:3], a[3] if len(a) == 4 else None, "--dry" in sys.argv, "--solo-play" in sys.argv)
