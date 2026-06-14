#!/usr/bin/env Rscript
# openspecy ftir_library.rds에서 미등록 스펙트럼 추출하여 RIST_FTIR_Library에 추가

library(data.table)

# ── 1. 데이터 로드 ────────────────────────────────────────────────
cat("RDS 파일 로드 중...\n")
lib  <- readRDS("data/openspecy/ftir_library.rds")
meta <- readRDS("data/openspecy/ftir_metadata.rds")

cat("스펙트럼 수:", nrow(meta), "\n")
cat("고유 소재:", length(unique(meta$spectrum_identity)), "\n\n")

# ── 2. 카테고리 매핑 정의 ─────────────────────────────────────────
# (spectrum_identity → list(category, display_name))
# "skip" = 이미 라이브러리에 있거나 불필요한 항목

category_map <- list(
  # ── 01_battery ────────────────────────────────────────────────
  "poly(ethylene glycol)"    = list("01_battery/01_electrolyte_solvents", "Polyethylene Glycol (PEG)"),
  "poly(ethylene oxide)"     = list("01_battery/02_binders_polymers",     "Polyethylene Oxide (PEO)"),

  # ── 03_engineering_plastic / 01_commodity ─────────────────────
  "HDPE"                                  = list("03_engineering_plastic/01_commodity", "Polyethylene High Density (HDPE)"),
  "polyethylene high density"             = list("skip", ""),
  "polyethylene low density"              = list("03_engineering_plastic/01_commodity", "Polyethylene Low Density (LDPE)"),
  "polyethylene low density linear"       = list("03_engineering_plastic/01_commodity", "Polyethylene Linear Low Density (LLDPE)"),
  "polyethylene chlorinated"              = list("03_engineering_plastic/01_commodity", "Chlorinated Polyethylene (CPE)"),
  "polyethylene chlorosulfonated"         = list("03_engineering_plastic/01_commodity", "Chlorosulfonated Polyethylene (CSM)"),
  "polyethylene foamed"                   = list("skip", ""),
  "polyethylene oxidized"                 = list("skip", ""),
  "polyethylene wax"                      = list("skip", ""),
  "polyethylene wax oxidized"             = list("skip", ""),
  "poly(1 butene) isotactic"              = list("03_engineering_plastic/01_commodity", "Poly(1-Butene) (PB-1)"),
  "poly(4 methyl 1 pentene)"              = list("03_engineering_plastic/01_commodity", "Poly(4-Methyl-1-Pentene) (PMP)"),
  "polypropylene isotactic"               = list("skip", ""),
  "polystyrene expanded"                  = list("03_engineering_plastic/01_commodity", "Expanded Polystyrene (EPS)"),
  "Styrofoam"                             = list("skip", ""),
  "poly(2 4 6 tribromostyrene)"           = list("03_engineering_plastic/01_commodity", "Poly(Tribromostyrene) (PTBS)"),
  "acrylonitrile butadiene styrene"       = list("skip", ""),
  "poly(vinyl acetate)"                   = list("03_engineering_plastic/01_commodity", "Polyvinyl Acetate (PVAc)"),
  "poly(vinyl chloride)"                  = list("skip", ""),
  "polyvinylchloride"                     = list("skip", ""),
  "polyvinylchloride with plasticizer"    = list("skip", ""),
  "poly(vinyl chloride) carboxylated"     = list("03_engineering_plastic/01_commodity", "Carboxylated PVC"),
  "vinyl chloride vinyl acetate"          = list("03_engineering_plastic/01_commodity", "Vinyl Chloride-Vinyl Acetate (VCVA)"),
  "vinyl chloride vinyl acetate hydroxypropyl acrylate" = list("skip", ""),
  "vinyl chloride vinyl acetate maleic acid"            = list("skip", ""),
  "vinylidene chloride acrylonitrile"     = list("03_engineering_plastic/01_commodity", "Vinylidene Chloride-Acrylonitrile (PVDC-AN)"),
  "vinylidene chloride vinyl chloride"    = list("03_engineering_plastic/01_commodity", "Vinylidene Chloride-Vinyl Chloride (PVDC)"),
  "styrene acrylonitrile"                 = list("skip", ""),   # 이미 라이브러리에 있음 (SAN)
  "ethylene ethyl acrylate"              = list("03_engineering_plastic/01_commodity", "Ethylene-Ethyl Acrylate Copolymer (EEA)"),
  "styrene maleic anhydride"              = list("03_engineering_plastic/01_commodity", "Styrene Maleic Anhydride (SMA)"),
  "styrene maleic anhydride partial methyl ester" = list("skip", ""),
  "styrene allyl alcohol"                 = list("03_engineering_plastic/01_commodity", "Styrene-Allyl Alcohol Copolymer (SAA)"),
  "styrene butyl methacrylate"            = list("skip", ""),
  "ethylene acrylic acid"                 = list("skip", ""),
  "ethylene methacrylic acid"             = list("skip", ""),
  "ethylene vinyl acetate"                = list("skip", ""),
  "ethylene vinyl alcohol"                = list("skip", ""),
  "ethylene propylene"                    = list("03_engineering_plastic/01_commodity", "Ethylene-Propylene Copolymer (EP)"),
  "PE with silicate inorganic"            = list("03_engineering_plastic/01_commodity", "PE/Silicate Composite"),
  "PE+silicate+bio"                       = list("skip", ""),
  "Polyethylene"                          = list("skip", ""),
  "Polypropylene  with silicate mix"      = list("03_engineering_plastic/01_commodity", "PP/Silicate Composite"),

  # ── 03_engineering_plastic / 02_engineering ───────────────────
  "poly(2 6 dimethyl p phenylene oxide)"  = list("03_engineering_plastic/02_engineering", "Poly(Phenylene Oxide) (PPO)"),
  "poly(p phenylene ether sulphone)"      = list("03_engineering_plastic/02_engineering", "Poly(Phenylene Ether Sulfone) (PES)"),
  "poly(phenylene sulfide)"              = list("03_engineering_plastic/02_engineering", "Poly(Phenylene Sulfide) (PPS)"),
  "polyphenylsulfone"                    = list("03_engineering_plastic/02_engineering", "Polyphenylsulfone (PPSU)"),
  "polysulfone"                          = list("03_engineering_plastic/02_engineering", "Polysulfone (PSU)"),
  "poly(2 hydroxyethyl methacrylate)"    = list("03_engineering_plastic/02_engineering", "Poly(HEMA)"),
  "poly(ethyl methacrylate)"             = list("03_engineering_plastic/02_engineering", "Poly(Ethyl Methacrylate) (PEMA)"),
  "poly(isobutyl methacrylate)"          = list("03_engineering_plastic/02_engineering", "Poly(Isobutyl Methacrylate) (PiBMA)"),
  "poly(n butyl methacrylate)"           = list("03_engineering_plastic/02_engineering", "Poly(n-Butyl Methacrylate) (PnBMA)"),
  "butyl methacrylate isobutyl methacrolate" = list("skip", ""),
  "poly(vinyl butyral)"                  = list("03_engineering_plastic/02_engineering", "Poly(Vinyl Butyral) (PVB)"),
  "poly(vinyl formal)"                   = list("03_engineering_plastic/02_engineering", "Poly(Vinyl Formal) (PVF)"),
  "poly(vinyl stearate)"                 = list("skip", ""),
  "polyacrylamide"                       = list("03_engineering_plastic/02_engineering", "Polyacrylamide (PAM)"),
  "polyacrylamide carboxyl modified"     = list("skip", ""),
  "poly(acrylic acid)"                   = list("skip", ""),
  "polyimide"                            = list("skip", ""),
  "polyacetal"                           = list("skip", ""),
  "polyoxymethylene"                     = list("skip", ""),
  "poly(butylene terephthalate)"         = list("skip", ""),
  "polybuthylene terephthalate"          = list("skip", ""),
  "PET"                                  = list("skip", ""),
  "polyethylene terephthalate"           = list("skip", ""),
  "poly(ethylene terephthalate)"         = list("skip", ""),
  "poly(ethylene terepthalate)"          = list("skip", ""),
  "polyethylene terephtalate"            = list("skip", ""),
  "polytehylene terephthalate"           = list("skip", ""),
  "polytehylene terephthalate amorphous" = list("03_engineering_plastic/02_engineering", "Amorphous PET (APET)"),
  "polyesterterpthalate"                 = list("skip", ""),
  "copolyester"                          = list("03_engineering_plastic/02_engineering", "Copolyester"),
  "PMMA"                                 = list("skip", ""),
  "poly(methyl methacrylate)"            = list("skip", ""),
  "polymethyl methacrylate"              = list("skip", ""),
  "polycarbonate"                        = list("skip", ""),
  "polyamide"                            = list("skip", ""),
  "polyamide 6"                          = list("skip", ""),
  "polyamide 66"                         = list("skip", ""),
  "polyamide resin"                      = list("skip", ""),
  "polymaide 66"                         = list("skip", ""),
  "copolyamide"                          = list("skip", ""),
  "nylon 6"                              = list("skip", ""),
  "nylon 6 (3)  T"                       = list("skip", ""),
  "nylon 6 6"                            = list("skip", ""),
  "nylon 6 9"                            = list("skip", ""),
  "nylon 6 12"                           = list("skip", ""),
  "nylon 11"                             = list("skip", ""),
  "nylon 12"                             = list("skip", ""),
  "Polyethereamide"                      = list("03_engineering_plastic/02_engineering", "Polyetheramide (PEBA)"),
  "polyetherester"                       = list("03_engineering_plastic/02_engineering", "Polyetherester (COPE)"),
  "methyl vinyl ether maleic acid"       = list("03_engineering_plastic/02_engineering", "Methyl Vinyl Ether-Maleic Acid Copolymer (MVE-MA)"),
  "methyl vinyl ether maleic anhydride"  = list("03_engineering_plastic/02_engineering", "Methyl Vinyl Ether-Maleic Anhydride Copolymer (MVE-MAn)"),
  "n vinylpyrrolidone vinyl acetate"     = list("skip", ""),
  "polyvinylpyrrolidone"                 = list("skip", ""),
  "poly(4 4' dipropoxy 2 2' diphenyl propane fumarate)" = list("skip", ""),
  "poly(diallyl isophthalate)"           = list("skip", ""),
  "poly(tetrafluoroethylene)"            = list("skip", ""),
  "polytetrafluoroethylene"              = list("skip", ""),
  "Teflon/PTFE"                          = list("skip", ""),
  "poly(vinylidene fluoride)"            = list("skip", ""),
  "polyurethane"                         = list("skip", ""),
  "Polyether urethane with additives"    = list("skip", ""),
  "polyurethane acrylic resin"           = list("skip", ""),
  "polyesterurethane"                    = list("skip", ""),
  "polyetherurethane"                    = list("skip", ""),
  "poly(vinyl alcohol)"                  = list("skip", ""),
  "polyvinyl alcohol"                    = list("skip", ""),
  "polyacrylamide carboxyl modified"     = list("skip", ""),
  "Acrylic"                              = list("skip", ""),
  "acrylonitrile butadiene"              = list("03_engineering_plastic/02_engineering", "Acrylonitrile-Butadiene Copolymer (NBR precursor)"),
  "polu butadiene acrylonitrile"         = list("skip", ""),
  "1 2 polybutadiene"                    = list("skip", ""),
  "Polyethylene with acryloid and pthalocyanine (blue)" = list("skip", ""),
  "Pthalate and propyl alcohol mix"      = list("skip", ""),
  "PVA with Kaolin clay"                 = list("skip", ""),
  "Polypropylene"                        = list("skip", ""),
  "Polystyrene"                          = list("skip", ""),
  "polyethylene"                         = list("skip", ""),
  "polypropylene"                        = list("skip", ""),
  "polystyrene"                          = list("skip", ""),
  "polylactic acid"                      = list("skip", ""),
  "polyhydroxybutyric acid"              = list("skip", ""),
  "PDMS"                                 = list("skip", ""),
  "phenoxy resin"                        = list("skip", ""),
  "epoxide resin"                        = list("skip", ""),
  "alkyd varnish"                        = list("skip", ""),
  "resin dispersion"                     = list("skip", ""),
  "lahmian medium acrylic paint"         = list("skip", ""),
  "polyester"                            = list("skip", ""),
  "Polyester"                            = list("skip", ""),
  "polyester epoxide"                    = list("03_engineering_plastic/02_engineering", "Polyester-Epoxide Hybrid"),
  "polycaprolactone"                     = list("03_engineering_plastic/03_bioplastics", "Polycaprolactone (PCL)"),
  "polyhydroxybutyric acid"              = list("skip", ""),
  "aramid"                               = list("03_engineering_plastic/02_engineering", "Aramid (Kevlar)"),
  "poly(2 6 dimethyl p phenylene oxide)" = list("03_engineering_plastic/02_engineering", "Poly(Phenylene Oxide) (PPO)"),
  "polychloroprene"                      = list("skip", ""),

  # ── 03_engineering_plastic / 03_bioplastics ───────────────────
  "cellulose"                            = list("03_engineering_plastic/03_bioplastics", "Cellulose"),
  "Cellulose"                            = list("skip", ""),  # 중복 방지
  "cellulose acetate"                    = list("03_engineering_plastic/03_bioplastics", "Cellulose Acetate (CA)"),
  "cellulose acetate butyrate"           = list("03_engineering_plastic/03_bioplastics", "Cellulose Acetate Butyrate (CAB)"),
  "cellulose propionate"                 = list("03_engineering_plastic/03_bioplastics", "Cellulose Propionate (CP)"),
  "cellulose triacetate"                 = list("03_engineering_plastic/03_bioplastics", "Cellulose Triacetate (CTA)"),
  "cellulose wipe"                       = list("skip", ""),
  "hydroxyethyl cellulose"              = list("03_engineering_plastic/03_bioplastics", "Hydroxyethyl Cellulose (HEC)"),
  "hydroxypropyl cellulose"             = list("03_engineering_plastic/03_bioplastics", "Hydroxypropyl Cellulose (HPC)"),
  "hydroxypropyl methyl cellulose"      = list("03_engineering_plastic/03_bioplastics", "Hydroxypropyl Methyl Cellulose (HPMC)"),
  "methyl cellulose"                    = list("03_engineering_plastic/03_bioplastics", "Methyl Cellulose (MC)"),
  "ethyl cellulose"                     = list("03_engineering_plastic/03_bioplastics", "Ethyl Cellulose (EC)"),
  "nitrocellulose"                       = list("03_engineering_plastic/03_bioplastics", "Nitrocellulose (NC)"),
  "Nitrocellulose"                       = list("skip", ""),
  "alginic acid  sodium salt"            = list("03_engineering_plastic/03_bioplastics", "Sodium Alginate"),
  "zein purified"                        = list("03_engineering_plastic/03_bioplastics", "Zein (Corn Protein)"),
  "polyhydroxy"                          = list("skip", ""),
  "Cardboard/cellulose"                  = list("skip", ""),
  "papercup_cellulosic"                  = list("skip", ""),

  # ── 04_elastomers_seals ───────────────────────────────────────
  "styrene ethylene butylene"            = list("04_elastomers_seals", "Styrene-Ethylene-Butylene-Styrene (SEBS)"),
  "styrene isoprene"                     = list("04_elastomers_seals", "Styrene-Isoprene Block Copolymer (SIS)"),
  "polyisoprene chlorinated"             = list("skip", ""),
  "acrylonitrile butadiene"              = list("skip", ""),  # 이미 위에서 처리
  "silicone rubber"                      = list("skip", ""),
  "silicone seal reactor"                = list("skip", ""),
  "silicone/PDMS"                        = list("skip", ""),
  "windscreen wiper rubber"              = list("04_elastomers_seals", "Wiper Blade Rubber"),
  "Plumbers tape sealing putty"          = list("skip", ""),
  # o-ring 관련 (이미 등록됨)
  "sealing ring EPDM"                    = list("skip", ""),
  "sealing ring Gardena 1124 large"      = list("skip", ""),
  "sealing ring Gardena 1124 small"      = list("skip", ""),
  "sealing ring Gardena 2824 large"      = list("skip", ""),
  "sealing ring Gardena 2824 medium"     = list("skip", ""),
  "sealing ring Gardena 2824 small"      = list("skip", ""),

  # ── 05_ceramic_inorganic ──────────────────────────────────────
  "quartz sand beach"                    = list("05_ceramic_inorganic", "Quartz (Beach Sand)"),
  "quartz sand lab"                      = list("05_ceramic_inorganic", "Quartz (Lab)"),
  "silica gel lab"                       = list("05_ceramic_inorganic", "Silica Gel"),
  "coal"                                 = list("05_ceramic_inorganic", "Coal"),
  "amber"                                = list("05_ceramic_inorganic", "Amber (Fossil Resin)"),

  # ── 06_natural_fibers (새 카테고리) ────────────────────────────
  "fibre acetate"                        = list("06_natural_fibers", "Acetate Fiber"),
  "fibre cocoanut"                       = list("skip", ""),
  "fibre cotton combers"                 = list("06_natural_fibers", "Cotton Fiber"),
  "fibre cotton US pima"                 = list("skip", ""),
  "fibre cotton uzbekistan"              = list("skip", ""),
  "fibre indian raw cotton"              = list("skip", ""),
  "fibre flax"                           = list("06_natural_fibers", "Flax Fiber"),
  "fibre hemp fine"                      = list("06_natural_fibers", "Hemp Fiber"),
  "fibre hemp rough"                     = list("skip", ""),
  "fibre jute"                           = list("06_natural_fibers", "Jute Fiber"),
  "fibre kapok"                          = list("skip", ""),
  "fibre linen"                          = list("06_natural_fibers", "Linen Fiber"),
  "fibre mulberry silk"                  = list("06_natural_fibers", "Silk Fiber"),
  "fibre silk slubbing"                  = list("skip", ""),
  "fibre tussah silk"                    = list("skip", ""),
  "fibre grass"                          = list("skip", ""),
  "fibre turf"                           = list("skip", ""),
  "fibre polyactide"                     = list("skip", ""),
  "fibre polyamide 6"                    = list("skip", ""),
  "fibre polyamide 6 (not)stretched"     = list("skip", ""),
  "fibre polyamide 6 P6.6"               = list("skip", ""),
  "fibre polyester"                      = list("skip", ""),
  "fibre polyetheretherketone"           = list("skip", ""),
  "fibre polypropylene"                  = list("skip", ""),
  "fibre polypropylene dyed"             = list("06_natural_fibers", "Dyed Polypropylene Fiber"),
  "fibre polyvinyl alcohol"              = list("skip", ""),
  "fibre polyvinylidene fluoride"        = list("skip", ""),
  "fibre poplar down"                    = list("skip", ""),
  "fibre roasted flax"                   = list("skip", ""),
  "fibre urtica dioica L conar fibra"    = list("skip", ""),
  "fibre viscose"                        = list("06_natural_fibers", "Viscose (Rayon)"),
  "fibre viscose dyed"                   = list("skip", ""),
  "fibre thermoplastic elastomere"       = list("04_elastomers_seals", "Thermoplastic Elastomer Fiber (TPE)"),
  "wool"                                 = list("06_natural_fibers", "Wool"),
  "wool cashmere crossbred"              = list("skip", ""),
  "wool cashmere kasakhstan"             = list("skip", ""),
  "wool cashmere mongolia"               = list("skip", ""),
  "wool raw cashmere afghanistan"        = list("skip", ""),
  "wool raw cashmere mongolia"           = list("skip", ""),
  "wool sheep supersoft"                 = list("skip", ""),
  "wool slubbing fine"                   = list("skip", ""),
  "wool slubbing rough"                  = list("skip", ""),
  "scoured wool  not made rough"         = list("skip", ""),
  "merino scoured wool made rough"       = list("skip", ""),
  "fur alpaca"                           = list("skip", ""),
  "fur angora rabbit"                    = list("skip", ""),
  "fur camel"                            = list("skip", ""),
  "fur cat european shorthair"           = list("skip", ""),
  "fur cow"                              = list("skip", ""),
  "fur dog"                              = list("skip", ""),
  "fur lama"                             = list("skip", ""),
  "fur mohair angora goat"               = list("skip", ""),
  "fur red deer"                         = list("skip", ""),
  "fur wild boar"                        = list("skip", ""),
  "fur yak"                              = list("skip", ""),
  "fur yak bleached"                     = list("skip", ""),
  # 생물/식품 재료 (제외)
  "algae desmarestia viridis"            = list("skip", ""),
  "algae fucus serratus"                 = list("skip", ""),
  "algae laminaria digita and hyperborea" = list("skip", ""),
  "algae laminaria sacharina"            = list("skip", ""),
  "black broodcomb"                      = list("skip", ""),
  "broodcomb"                            = list("skip", ""),
  "broodcomb once brooded"               = list("skip", ""),
  "honeycomb"                            = list("skip", ""),
  "honeycomb freshly removed"            = list("skip", ""),
  "honeycomb freshly removed with nectar" = list("skip", ""),
  "honeycomb middle wall"                = list("skip", ""),
  "honeycomb top bar"                    = list("skip", ""),
  "chitin cancer pagurus"                = list("skip", ""),
  "chitin crangon antonia"               = list("skip", ""),
  "chitin from crustacean shells"        = list("skip", ""),
  "crangon  chitin exuvie"               = list("skip", ""),
  "cigarette filter"                     = list("skip", ""),
  "wood beech"                           = list("skip", ""),
  "wood glue"                            = list("skip", ""),
  "wood mahagoni"                        = list("skip", ""),
  "wood pine"                            = list("skip", ""),
  "leaf-plant-"                          = list("skip", ""),
  "styrene butadiene"                    = list("skip", ""),
  "nitrile rubber"                       = list("skip", ""),
  "PDMS"                                 = list("skip", ""),
  "polychloroprene"                      = list("skip", "")
)

# ── 3. 출력 디렉토리 생성 ─────────────────────────────────────────
base_dir <- "data/RIST_FTIR_Library"
dirs_needed <- unique(sapply(Filter(function(x) x[[1]] != "skip", category_map), `[[`, 1))
for (d in dirs_needed) {
  full_d <- file.path(base_dir, d)
  if (!dir.exists(full_d)) {
    dir.create(full_d, recursive=TRUE)
    cat("디렉토리 생성:", full_d, "\n")
  }
}

# ── 4. 스펙트럼 내보내기 ──────────────────────────────────────────
# lib$sample_name: 1~636 정수 (meta의 행 번호와 일치)
setDT(lib)
setDT(meta)

# meta에 인덱스 컬럼 추가
meta[, row_idx := .I]

manifest_rows <- list()
skipped <- 0
exported <- 0

# 각 spectrum_identity별로 처리
identities <- unique(meta$spectrum_identity)

for (ident in identities) {
  # 매핑 확인
  mapping <- category_map[[ident]]
  if (is.null(mapping)) {
    cat("경고: 매핑 없음 - '", ident, "'\n", sep="")
    next
  }
  if (mapping[[1]] == "skip") {
    skipped <- skipped + 1
    next
  }

  category    <- mapping[[1]]
  display_name <- mapping[[2]]

  # 해당 identity의 행 번호 목록
  idx_rows <- meta[spectrum_identity == ident, row_idx]

  # 파일명용 안전한 prefix 생성
  safe_name <- tolower(display_name)
  safe_name <- gsub("[^a-z0-9]+", "_", safe_name)
  safe_name <- gsub("_+", "_", safe_name)
  safe_name <- gsub("^_|_$", "", safe_name)
  safe_name <- paste0("openspecy_", safe_name)

  out_dir <- file.path(base_dir, category)

  for (i in seq_along(idx_rows)) {
    sn <- idx_rows[i]
    spectrum <- lib[sample_name == sn, .(wavenumber, intensity)]
    if (nrow(spectrum) == 0) next

    # 파일명 (여러 개이면 번호 붙임)
    if (length(idx_rows) == 1) {
      fname <- paste0(safe_name, ".csv")
    } else {
      fname <- paste0(safe_name, sprintf("-%d", i), ".csv")
    }
    fpath <- file.path(out_dir, fname)

    # 이미 존재하면 건너뜀
    if (file.exists(fpath)) next

    # 웨이브넘버 정렬
    spectrum <- spectrum[order(wavenumber)]
    setnames(spectrum, c("wavenumber", "absorbance"))

    write.csv(spectrum, fpath, row.names=FALSE)
    exported <- exported + 1

    # manifest 행 추가
    rel_path <- file.path(category, fname)
    manifest_rows[[length(manifest_rows)+1]] <- data.frame(
      file          = rel_path,
      material      = display_name,
      source        = "openspecy",
      intensity_type = "absorbance",
      n_points      = nrow(spectrum),
      wn_min        = min(spectrum$wavenumber),
      wn_max        = max(spectrum$wavenumber),
      stringsAsFactors = FALSE
    )
  }
}

cat("\n=== 결과 ===\n")
cat("내보낸 스펙트럼:", exported, "\n")
cat("건너뜀:", skipped, "\n")

if (length(manifest_rows) > 0) {
  manifest_df <- rbindlist(manifest_rows)
  write.csv(manifest_df, "openspecy_new_manifest.csv", row.names=FALSE)
  cat("manifest 저장:", nrow(manifest_df), "행 → openspecy_new_manifest.csv\n")
} else {
  cat("새로운 스펙트럼 없음\n")
}
