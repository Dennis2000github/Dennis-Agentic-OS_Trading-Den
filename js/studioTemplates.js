/* =====================================================================
   STUDIO · Ads Studio template catalogs (Step 5 Production dropdowns)

   Two curated template sets that supply prompt text for the final video
   generation prompt:

   * ADS_ENV_TEMPLATES  — "Model Placement & Environment": where and how the
     model/product is staged. Selecting one inserts its text as the
     environment/placement direction.
   * ADS_VIDEO_TEMPLATES — "Video Generation Templates": how the model moves
     on camera. Selecting one inserts its text as the motion direction.

   Both are plain data so the dropdowns in Step 5 are rendered from here.
   ===================================================================== */
(function () {
  "use strict";

  var ENV_TEMPLATES = [
    {
      group: "High-Fashion Editorial & Avant-Garde",
      items: [
        "Brutalist raw concrete cube with overhead circular skylight",
        "Monochromatic red room with high-gloss vinyl surfaces",
        "Mirrored geometric maze with infinite reflections",
        "Sculptural white museum hall with abstract plaster pillars",
        "Draped crimson velvet soundstage with dramatic side key light",
        "Minimalist chrome tunnel with diffuse overhead LED panels",
        "Liquid latex glossy floor with soft diffused backlighting",
        "High-concept sculptural pavilion with fluid curved walls",
        "Matte black cyclorama with single intense top spotlight",
        "Deconstructed architectural warehouse with exposed steel mesh",
        "Oversized paper installation set with sharp directional shadows",
        "All-white padded minimalist room with soft perimeter glow",
        "Geometric neon grid room with dark reflective floor",
        "Industrial hangar featuring floating translucent fabric panels",
        "Metallic foil-wrapped studio set with direct strobe lighting",
        "Wet reflective black glass platform suspended in darkness",
        "Monolithic stone pillars inside a minimalist concrete atrium",
        "Minimalist yellow studio box with harsh graphic midday shadows",
        "Kinetic light tube installation in a dark void space",
        "Transparent acrylic podium inside an empty gallery",
        "Abstract foam sculpture set with soft pastel gradient lighting",
        "Perforated metal mesh corridor with back-projected light patterns",
        "Hyper-stylized monochrome beige room with sharp box shadows",
        "Floating mirror installation on a polished granite surface",
        "Minimalist glass prism room with prismatic color dispersion",
        "Industrial steel staircase set against raw weathered zinc panels",
        "High-gloss lacquer white cube with rim-lit blue neon edge",
        "Angular geometric plaster alcoves with deep contrast shadows",
        "Conceptual runway platform floating over shallow dark water",
        "Sculptural stone garden inside an ultra-modern white gallery"
      ]
    },
    {
      group: "Modern Luxury & High-End Architecture",
      items: [
        "Ultra-modern cliffside villa terrace overlooking the Mediterranean",
        "Private superyacht sundeck with teak flooring at anchor",
        "Designer penthouse lounge with double-height floor-to-ceiling glass",
        "Subterranean private wine cellar with backlit onyx display",
        "Cantilevered infinity pool edge above a tropical valley",
        "Marble-clad master bathroom with freestanding soaking tub",
        "High-end art collector's residence with bespoke walnut paneling",
        "Luxury penthouse balcony with panoramic skyline views",
        "Private jet interior cabin with cream leather executive seating",
        "Modernist glass pavilion surrounded by a minimalist reflecting pool",
        "Five-star presidential suite living room with custom designer furniture",
        "Grand minimalist limestone corridor with floor-recessed uplights",
        "Sleek brutalist villa courtyard featuring a single ancient olive tree",
        "Floating glass staircase inside a contemporary architectural mansion",
        "Luxury sports car garage showroom with polished resin floors",
        "Modern Swiss alpine chalet with panoramic thermal glass walls",
        "High-end designer boutique VIP salon with plush mohair seating",
        "Private rooftop helipad on a modern residential tower",
        "Luxury resort open-air pavilion over calm turquoise water",
        "Custom walnut and brass walk-in wardrobe with soft backlit shelving",
        "Contemporary art gallery courtyard with smooth travertine paving",
        "Minimalist concrete beach house patio at sunset",
        "Oceanfront modernist patio with recessed fire pit lounge",
        "High-end wellness spa lounge with fluted marble walls",
        "Sculptural concrete entry foyer of a luxury hillside residence",
        "High-end equestrian estate stable aisle with polished brick floors",
        "Designer watch showroom private lounge with brushed gold accents",
        "Modern desert glass villa with continuous polished concrete floors",
        "Private yacht salon with panoramic ocean views",
        "Exclusive members-only rooftop lounge with teak pergolas"
      ]
    },
    {
      group: "Historic Grandeur & Classical Heritage",
      items: [
        "French Baroque ballroom with gilded moldings and crystal chandeliers",
        "18th-century European palace gallery with ornate frescoed ceilings",
        "Grand neoclassical marble staircase with ornate wrought iron railings",
        "Historic Parisian apartment salon with herringbone parquet floors",
        "Ancient Italian palazzo loggia with weathered stone arches",
        "Gothic cathedral cloister corridor with dramatic vaulted ceilings",
        "Old-world private library with multi-tier carved oak bookshelves",
        "Victorian botanical glasshouse with curved wrought iron frames",
        "Historic Venetian palazzo balcony overlooking the Grand Canal",
        "Grand opera house private viewing box with red velvet drapes",
        "Spanish colonial hacienda courtyard with terracotta tiles and fountain",
        "Classical European museum gallery flanked by marble statues",
        "Tudor-era stone manor hall with a grand walk-in stone fireplace",
        "Rococo salon with pastel wall boiserie and antique gilded mirrors",
        "Ancient stone fortress courtyard with dramatic archways",
        "Neoclassical portico with soaring fluted Corinthian columns",
        "Historic English country estate drawing room with floral silk wallpaper",
        "Antique conservatory with weathered checkerboard marble flooring",
        "19th-century royal banquet hall with long mahogany dining tables",
        "Historic Italian villa terrace with classical stone balustrades",
        "Renaissance courtyard garden with symmetry and stone statuary",
        "Antique tapestry room in an authentic European chateau",
        "Historic theater stage framed by heavy velvet stage curtains",
        "Classical colonnade walkway surrounded by manicured topiary",
        "Ornate historic vault room with stone groin-vault ceilings",
        "Vintage palace mirror hall reflecting ornate gold stucco work",
        "Traditional courtyard of a historic Kyoto ryokan",
        "Classical domed rotunda with natural light filtering from the oculus",
        "Heritage French chateau wine tasting room with exposed limestone",
        "Classical music hall stage with curved wooden acoustic shells"
      ]
    },
    {
      group: "Futuristic & Cyberpunk Aesthetics",
      items: [
        "Neon-lit Tokyo alleyway with holographic billboards and wet asphalt",
        "Futuristic transit hub corridor with illuminated hexagonal walls",
        "Cyberpunk rooftop overlooking dense layers of glowing skyscrapers",
        "High-tech laboratory cleanly lit with cold white LED strip lights",
        "Futuristic subterranean tunnel with pulsing cyan and magenta lights",
        "Sci-fi spaceship bridge with panoramic digital starfield windows",
        "Dystopian industrial catwalk suspended over foggy pipe networks",
        "Futuristic glass skybridge connecting two ultra-tall mega towers",
        "Neon-washed elevated railway platform at midnight",
        "Cyberpunk night market stall corridor with hanging glowing signs",
        "High-tech server room with endless racks of blinking server lights",
        "Futuristic luxury capsule apartment with curved ergonomic walls",
        "Dark subterranean bunker with heavy automated blast doors",
        "Holographic projection chamber with a dark reflective pool floor",
        "Cyberpunk subterranean parking deck with glowing floor markings",
        "Sleek white android assembly facility with sterile ring lighting",
        "Futuristic monorail interior with illuminated transparent screens",
        "Sci-fi hangar bay with massive hexagonal exterior hangar doors",
        "Neon-drenched rainy street corner with reflective storefronts",
        "Futuristic observation deck overlooking a neon-lit megacity",
        "Cyberpunk mechanic garage filled with exposed wires and retrofitted tech",
        "Holographic runway stage with digital wireframe projections",
        "Sci-fi medical bay with sleek transparent isolation pods",
        "Futuristic subterranean subway platform with digital advertising walls",
        "Dark alleys with glowing cyber-tech graffiti and steam vents",
        "Futuristic skyscraper exterior maintenance platform at night",
        "Hi-tech control room with wrap-around glowing interface panels",
        "Cyberpunk elevated walkway with low-hanging neon utility pipes",
        "Futuristic botanical biodome with glowing bioluminescent plants",
        "Sci-fi spacecraft airlock chamber with industrial warning lights"
      ]
    },
    {
      group: "Urban Street & Contemporary Lifestyle",
      items: [
        "Busy Manhattan crosswalk with yellow cabs and steam rising",
        "Trendy Soho cobblestone street framed by classic cast-iron facades",
        "Minimalist concrete skatepark with smooth curves and hard shadows",
        "Vibrant London mews street lined with colorful historic row houses",
        "Sun-drenched Brooklyn brownstone front stoop and tree-lined street",
        "Industrial loading dock with corrugated metal shutters and concrete ramps",
        "Modern commercial bridge with geometric steel arches",
        "Hip downtown specialty coffee roastery with exposed brick walls",
        "Urban graffiti alley with vibrant multi-layered street art",
        "Sleek subway station concourse with clean white subway tiles",
        "Modern multi-story parking garage top deck with open city vistas",
        "Paris boulevard sidewalk cafe setting with bistro tables",
        "Trendy open-concept loft apartment with tall factory-style windows",
        "Urban rooftop garden overlooking a bustling downtown core",
        "Crosswalk outside a monolithic glass-and-steel skyscraper entrance",
        "Trendy streetwear pop-up store interior with industrial display racks",
        "Downtown warehouse exterior with brick walls and steel fire escapes",
        "Bustling European plaza with cobblestones and fountain seating",
        "Sleek underground pedestrian underpass with linear fluorescent tubes",
        "Modern city bus stop shelter on a rain-slicked boulevard",
        "Chic boutique hotel entrance with glowing minimalist canopy",
        "Downtown street corner outside an upscale retail flagship store",
        "Minimalist urban plaza with modern granite benches and water steps",
        "Trendy vinyl record lounge with warm wood and ambient warm lighting",
        "Narrow historic alleyway in an old European metropolitan center",
        "Urban basketball court with painted concrete surface at golden hour",
        "Modern glass office building atrium with multi-tier indoor balconies",
        "Open-air urban food market pavilion with overhead string lights",
        "Modern pedestrian promenade along a contemporary city riverfront",
        "Hip urban barber shop and styling studio with vintage modern interior"
      ]
    },
    {
      group: "Natural Wilderness & Dramatic Landscapes",
      items: [
        "Black sand volcanic beach framed by dramatic ocean surf",
        "Misty Redwood forest floor illuminated by sharp diagonal sun rays",
        "Towering rocky ocean sea stacks on an overcast Pacific coastline",
        "Windswept desert sand dunes with sharp, rippling wind ridges",
        "Dramatic Norwegian fjord edge looking down into deep blue water",
        "Rugged red rock canyon gorge bathed in warm reflected sunlight",
        "Vast white salt flats creating seamless horizon reflections",
        "Dense tropical rainforest riverbank surrounded by broadleaf ferns",
        "Jagged alpine mountain ridge surrounded by swirling mist",
        "Icy glacial lagoon dotted with floating crystal-clear icebergs",
        "Deep bamboo forest pathway with filtered green canopy lighting",
        "Rocky volcanic plateau with steaming geothermal vents",
        "Secluded mountain pine forest clearing dusted with fresh snow",
        "Dramatic seaside cliff ledge dropping vertically into the ocean",
        "High-altitude mountain lake shore with polished stone pebbles",
        "Dense misty cloud forest trail draped in moss and hanging vines",
        "Sun-drenched rocky scrubland in the Mediterranean hills",
        "Wide open golden savannah grassland with distant acacia trees",
        "Emerald green mossy canyon walls surrounding a narrow stream",
        "Rugged limestone karst landscape rising out of calm coastal waters",
        "Natural sea cave entrance with turquoise water reflections",
        "Towering pine forest ridge overlooking an expansive alpine valley",
        "Windswept coastal sand dunes covered with wild beach grass",
        "Dramatic glacial ice cave interior with vibrant translucent blue ice",
        "Mountain river valley with rapids cutting through granite boulders",
        "Stepped emerald-green agricultural terraces on a steep hillside",
        "Dense birch forest with stark white tree trunks and leaf carpet",
        "Rugged volcanic mountain caldera with dark obsidian rock fields",
        "Vast arctic tundra plain under an expansive overcast grey sky",
        "Dramatic natural sandstone arch in an arid desert basin"
      ]
    },
    {
      group: "Tropical Resort & Coastal Escape",
      items: [
        "Five-star tropical island overwater bungalow wooden walkway",
        "Secluded white sand cove surrounded by smooth granite boulders",
        "Sunken lounge area inside an infinity pool overlooking the ocean",
        "Open-air bamboo luxury eco-resort lounge open to the jungle",
        "Palm-fringed private beach path with soft dappled sunlight",
        "Oceanfront thatched cabana with billowing white linen curtains",
        "Modern beach club daybed terrace overlooking turquoise waters",
        "Luxury coastal resort open-air dining deck at golden hour",
        "Sunset catamaran sailing deck with clear panoramic sea views",
        "Coastal cliffside path lined with bougainvillea and whitewashed walls",
        "Private beachfront plunge pool surrounded by lush tropical greenery",
        "Minimalist concrete beach pavilion open to the ocean breeze",
        "Sun-drenched resort courtyard featuring tall royal palm trees",
        "Luxury yacht teak swim platform level with clear ocean water",
        "Tropical lagoon edge lined with dense coastal mangrove roots",
        "Open-air seaside spa pavilion with polished dark wood flooring",
        "Private island sandbar surrounded by shallow transparent waters",
        "Coastal seaside terrace paved with natural flat flagstones",
        "Modern oceanfront pool deck featuring minimalist daybeds",
        "Whitewashed Mediterranean seaside staircase leading to the water",
        "Luxury safari lodge viewing deck overlooking a waterhole",
        "Exotic botanical resort greenhouse pathway with hanging orchids",
        "Sun-drenched wooden boardwalk winding through coastal dunes",
        "Tropical villa outdoor shower garden surrounded by natural stone walls",
        "Coastal resort infinity bar with panoramic views of the horizon",
        "Private oceanside rocky alcove with natural seawater rock pools",
        "Shaded coconut grove clearing near a calm ocean shore",
        "Modern beachfront villa lawn sloping directly down to the sand",
        "Open-air tropical pavilion with high vaulted thatched ceilings",
        "Sunset viewing platform on an elevated ocean cliff"
      ]
    },
    {
      group: "Industrial & Underground Spaces",
      items: [
        "Decommissioned power plant turbine hall with exposed steel framing",
        "Raw industrial warehouse with massive rusted iron structural beams",
        "Subterranean concrete drainage cistern with dramatic monolithic pillars",
        "Abandoned aircraft hangar with massive weathered sliding doors",
        "Old brick textile factory floor with tall multi-pane iron windows",
        "Heavy industrial cargo ship dry dock with towering steel walls",
        "Underground concrete parking bunker with heavy exhaust ductwork",
        "Decommissioned railway roundhouse with rusted turntable tracks",
        "Raw concrete industrial basement with exposed ceiling utility pipes",
        "Industrial metal foundry hall with dramatic contrast lighting",
        "Abandoned industrial brewery cellar with massive stainless steel vats",
        "Concrete industrial rooftop surrounded by commercial air handlers",
        "Industrial shipping container terminal corridor between stacked boxes",
        "Old stone grain silo interior with single overhead light shaft",
        "Raw industrial freight elevator shaft with heavy wire cage doors",
        "Underground metro maintenance tunnel with exposed wiring conduits",
        "Heavy industrial steel fabrication workshop with overhead gantry cranes",
        "Disused boiler room with complex networks of brass and iron pipes",
        "Abandoned brick train depot with broken skylight panels",
        "Industrial chemical plant pipe gallery with overhead metal catwalks",
        "Underground storm tunnel with natural curved concrete architecture",
        "Raw concrete loading tunnel with recessed halogen work lights",
        "Industrial ship engine room with multi-level steel grated walkways",
        "Abandoned automotive plant assembly floor with clear floor space",
        "High-voltage industrial substation field with stark electrical towers",
        "Subterranean stone quarry cavern with rough-hewn rock walls",
        "Old industrial printing press floor with concrete support pillars",
        "Heavy industrial dockyard wharf with massive steel mooring bollards",
        "Raw concrete brutalist bunker interior with deep slit windows",
        "Abandoned airport maintenance facility with vast open polished floor"
      ]
    },
    {
      group: "Minimalist Studio & Clean Commercial Sets",
      items: [
        "Infinite pure white seamless cyclorama studio with soft diffuse overhead light",
        "Deep matte black studio backdrop with sharp dramatic rim lighting",
        "Smooth warm grey concrete floor studio with neutral grey wall",
        "High-key commercial studio setup with expansive softbox arrays",
        "Low-key dramatic portrait studio with focused snoot spotlighting",
        "Neutral textured limestone plaster backdrop wall with soft side light",
        "Seamless pastel beige photography studio with warm ambient glow",
        "Minimalist studio set with floating pure white geometric display cubes",
        "Glossy white reflective acrylic floor studio with clean rim highlights",
        "Modern editorial studio with textured off-white limewash backdrop",
        "Studio setup with sharp sunlight patterns through slatted window blinds",
        "Clean monochrome grey photography stage with soft shadow gradients",
        "Studio set with translucent frosted glass partitions and backlighting",
        "Minimalist studio interior with light natural oak wood paneling",
        "Pure white photography cyc with dramatic hard-edged projection light",
        "Clean architectural studio set with smooth curved plaster corner",
        "Studio setting with pure black reflective water shallow pool floor",
        "Minimalist concrete cyclorama studio with top-mounted grid lights",
        "Textured raw canvas photography backdrop in neutral earth tones",
        "Clean editorial studio corner with subtle warm cream paint finish",
        "Minimalist studio set featuring oversized abstract archway cutouts",
        "Monochromatic slate grey studio with overhead floating diffuse panel",
        "Clean commercial studio set with polished light travertine tiles",
        "Studio setup with subtle gradient color wash across seamless background",
        "Minimalist portrait nook with smooth curved clay-plaster walls",
        "Pure white commercial stage with sharp geometric shadow projections",
        "Studio set featuring a single textured sandstone monolithic slab",
        "Clean minimalist set with suspended brushed aluminum panel screens",
        "Warm taupe photography studio with soft bounce umbrella lighting",
        "High-gloss grey studio deck with sharp, high-contrast rim lighting"
      ]
    },
    {
      group: "Romantic Seasonal & Atmospheric Moods",
      items: [
        "Golden hour wildflower meadow bathed in warm, low-angle sunlight",
        "Misty autumn forest path blanketed in fallen golden birch leaves",
        "Spring cherry blossom park with falling pink petals and morning light",
        "Winter city street corner with gently falling snow and warm shop windows",
        "Lavender field in full bloom at sunrise with soft purple haze",
        "Sun-drenched sunflower field during late afternoon golden hour",
        "Moody rain-slicked city boulevard with glowing reflections of streetlights",
        "Ethereal morning park trail blanketed in heavy, low-lying ground fog",
        "Sunset ocean beach with wet sand reflecting deep orange sky tones",
        "Twilight mountain overlook illuminated by soft pink alpenglow",
        "Autumn vineyard lane with gold and amber grapevines at sunrise",
        "Winter pine forest trail following a heavy blanket of fresh powder snow",
        "Sun-drenched olive grove with dry golden grass during late afternoon",
        "Spring wisteria canopy tunnel with hanging clusters of purple blossoms",
        "Moody coastal sea wall on a stormy, overcast afternoon",
        "Warm summer wheat field with tall stalks swaying in the breeze",
        "Misty mountain valley at daybreak with distant layers of blue ridges",
        "Golden hour lakeside dock with smooth water reflecting the sunset",
        "Early spring botanical garden with blooming magnolia and tulip trees",
        "Foggy coastal lighthouse promontory during a cold morning dawn",
        "Autumn Japanese maple garden with vibrant scarlet leaves and stone paths",
        "Softly lit city park under a canopy of illuminated snow-covered trees",
        "Wild coastal heather moorland under sweeping dramatic storm clouds",
        "Sun-drenched countryside dirt road bordered by tall wild summer grass",
        "Winter alpine meadow with sparkling ice crystals in morning sunlight",
        "Early morning citrus orchard with dew on the leaves and warm sun rays",
        "Calm lake surface covered in morning mist with subtle pastel reflections",
        "Autumn park avenue flanked by soaring, vibrant yellow ginkgo trees",
        "Dramatic twilight coastal landscape with deep indigo and purple skies",
        "Sun-drenched poppy field on a gently rolling hillside at late afternoon"
      ]
    }
  ];

  var VIDEO_TEMPLATES = [
    {
      group: "Runway, Walk & Movement",
      items: [
        "Cinematic low-angle tracking shot, full-body framing. The model executes a sharp, rhythmic runway walk forward, stops with one hip cocked, elegant subtle chin lift, dramatic fabric flow, high-fashion luxury runway aesthetic. No dialogue, no subtitles.",
        "Smooth tracking dolly shot. She glides 4 steps forward towards camera, pivots gracefully on one heel, turns back over her shoulder with an intense gaze, high-end editorial campaign look. No dialogue, no subtitles.",
        "Steadicam tracking shot moving diagonally. The model walks dynamically across the frame, stops confidently in a wide stance, adjusts lapels/collar, sharp fabric drape, high-fashion energy. No dialogue, no subtitles.",
        "Eye-level medium-full tracking shot. Confident energetic power walk forward, sudden graceful deceleration, hands casually resting on waist, commanding high-fashion attitude. No dialogue, no subtitles.",
        "Slow-motion lateral tracking shot. She walks sideways in profile, gracefully shifts her torso towards the camera for a split second, long fabric billowing seamlessly. No dialogue, no subtitles.",
        "Slightly shaky cinematic shoulder-height follow shot. Casual yet stylish urban model walks forward, drops one shoulder slightly, effortless streetwear editorial posture. No dialogue, no subtitles.",
        "Ultra-smooth 60fps slow-motion tracking shot. Model takes 2 deliberate, rhythmic steps forward, hips swaying subtly to highlight the garment's tailoring, luxury lookbook mood. No dialogue, no subtitles.",
        "Medium full-body shot. Walking towards the camera, she executes a fluid 180-degree pivot turn, pauses facing away to showcase the back design, then casts a sharp backward glance. No dialogue, no subtitles.",
        "Cinematic wide tracking shot. Model moves forward with an effortless cross-step gait, lightly touches sunglasses/accessories, natural fabric physics, minimalist fashion commercial. No dialogue, no subtitles.",
        "Forward moving wide-to-medium tracking shot. Model walks straight down the runway with strong posture, pauses into an authoritative high-fashion stance, piercing eye contact. No dialogue, no subtitles."
      ]
    },
    {
      group: "360\u00B0, Orbital & Fluid Angles",
      items: [
        "360-degree continuous orbital camera move around the model. She slowly shifts weight while rotating in the opposite direction, effortlessly showcasing all angles of the outfit, luxury brand campaign. No dialogue, no subtitles.",
        "Smooth semi-circular arc shot from left profile to front. Model slowly turns her head to meet the lens, gentle hand positioning against hip, soft cinematic rim lighting. No dialogue, no subtitles.",
        "Low-angle spiral orbit shot. The model twists her upper torso gracefully, coat/dress flares subtly with physics-based movement, commanding high-fashion perspective. No dialogue, no subtitles.",
        "Camera orbits closely around the model's shoulders then widens out, model gently adjusts neckline, smooth transition into full silhouette, editorial elegance. No dialogue, no subtitles.",
        "Elevated high-angle orbital shot circling the model. Model poses gracefully looking upwards toward the lens, soft weight transition, architectural luxury mood. No dialogue, no subtitles.",
        "Fast-to-slow orbital camera wrap. Model freezes momentarily into a sharp angular pose, then softly drops shoulders, ultra-modern luxury aesthetic. No dialogue, no subtitles.",
        "Camera sweeps smoothly around from the side to the back of the model. She slightly twists her waist, highlighting the silhouette and garment structure. No dialogue, no subtitles.",
        "Quarter-turn orbit. Model turns 90 degrees from profile to direct front, softly brushing fingers over the fabric surface, refined editorial aesthetic. No dialogue, no subtitles.",
        "Steadicam sweeping in a wide semi-circle around the model standing motionless, eyes following the lens with subtle confidence, high-end lookbook video. No dialogue, no subtitles.",
        "Ethereal floating circular camera move. Model slowly rolls shoulders back, looks calmly into the camera, fabric reacting softly to subtle body movement. No dialogue, no subtitles."
      ]
    },
    {
      group: "Wind, Fluidity & Fabric Motion",
      items: [
        "Cinematic slow tracking shot. Moderate studio wind naturally catches hair and loose fabric/dress, model maintains a steady, relaxed editorial gaze, high fashion movement. No dialogue, no subtitles.",
        "Subtle push-in shot with soft wind blowing from the side. Delicate fabric ripples naturally, model lifts one arm slightly to guide the garment, dreamy luxury aesthetic. No dialogue, no subtitles.",
        "Medium full-body shot. Model turns briskly, causing the jacket/overcoat to billow outward dramatically, settling into a rigid high-fashion stance. No dialogue, no subtitles.",
        "Super slow-motion full framing. Gentle updraft lifts light textile layers, model takes a soft step forward, poetic and ethereal luxury fashion film vibe. No dialogue, no subtitles.",
        "Backlit cinematic shot. Wind moves through the hair and hemline, model turns slowly towards the light, confident facial expression, golden hour campaign mood. No dialogue, no subtitles.",
        "Medium-shot tracking. Model rotates hips quickly, letting the skirt/dress create a fluid circular wave before returning to a static pose. No dialogue, no subtitles.",
        "Side-angle tracking shot. Gentle wind pushes hair away from her face, she raises chin slightly, showing off necklace/collar details, clean editorial aesthetic. No dialogue, no subtitles.",
        "Wide tracking shot in ambient breeze. Natural air currents create dynamic fabric tension, model walks 2 steps and pauses with an effortless look. No dialogue, no subtitles.",
        "Medium framing. Model performs a slight body shake, allowing the textured fabric to flutter and drape naturally around the body, premium texture showcase. No dialogue, no subtitles.",
        "Slow push-in on full-body framing. A gentle gust of wind sweeps the dress backwards as the model steps forward, intense runway fashion presence. No dialogue, no subtitles."
      ]
    },
    {
      group: "Editorial, Studio & Multi-Pose Sequences",
      items: [
        "Static camera with micro push-in. Model effortlessly transitions through three distinct high-fashion editorial poses: hand on hip, hands crossed, slight head tilt with confident gaze. No dialogue, no subtitles.",
        "Medium shot. Model shifts weight from left leg to right, drops one shoulder, and rolls head slowly with a sophisticated haute couture expression. No dialogue, no subtitles.",
        "Clean medium-full framing. Symmetrical studio stance, model raises both hands slowly to adjust the cuffs, then drops arms naturally to sides. No dialogue, no subtitles.",
        "Medium-wide shot. Model strikes an angular, asymmetric pose creating triangular arm shapes, modern high-concept fashion campaign vibe. No dialogue, no subtitles.",
        "Subtle tracking shot. Model leans slightly back against an invisible plane, one hand touching the waistline, soft natural breathing and poised expression. No dialogue, no subtitles.",
        "Medium shot. Model slips both hands into pockets, pushes jacket back to reveal the inner layer, looks down then directly into the lens with a subtle smile. No dialogue, no subtitles.",
        "Full-body framing. Model fluidly transitions her body into an S-curve silhouette pose, accentuating the waistline and garment cut, Vogue editorial style. No dialogue, no subtitles.",
        "Subtle push-in on upper body. Model drops both shoulders, elongates the neck, and gives an intense high-fashion eye contact (smize), ultra-chic commercial look. No dialogue, no subtitles.",
        "Medium framing. Model sits elegantly on a minimalist block/chair, crosses legs slowly, adjusts lapel, polished luxury lookbook mood. No dialogue, no subtitles.",
        "Quick micro-pan into static pose. Model snaps into a wide-stance fashion pose, relaxes fingers along the thigh, confident commercial presence. No dialogue, no subtitles."
      ]
    },
    {
      group: "Camera Movement, Lens Transitions & Micro-Interactions",
      items: [
        "Cinematic camera starts on an ultra-close-up of the fabric/buttons and smoothly pulls back to reveal the full-body outfit and model's confident pose. No dialogue, no subtitles.",
        "Slow continuous dolly-in from full-body to medium close-up. Model maintains steady, captivating eye contact while subtly angling her chin, luxury perfume/fashion ad vibe. No dialogue, no subtitles.",
        "Smooth vertical tilt-up shot starting from the footwear/heels, moving up through the garment drape, ending on the model's poised facial expression. No dialogue, no subtitles.",
        "Medium-close framing. Model gently buttons or adjusts the collar with refined hand movements, turns profile to camera, luxury tailoring aesthetic. No dialogue, no subtitles.",
        "High-angle jib shot smoothly dropping down to eye level. Model looks up to meet the descending lens, pausing in a relaxed high-fashion stance. No dialogue, no subtitles.",
        "Slight Dutch angle tracking shot moving dynamically, slowly leveling out as the model comes to an abrupt, stylish stop. Edgy modern streetwear editorial. No dialogue, no subtitles.",
        "Fast tracking shot from the side profile, decelerates quickly as model turns 45 degrees towards the camera, snapping a fierce editorial look. No dialogue, no subtitles.",
        "Medium shot. Model runs fingers slowly down the sleeve cuff to showcase ring/accessories, raises eyes to lens, ultra-clean commercial look. No dialogue, no subtitles.",
        "Soft whip-pan camera move landing sharply on the model standing in a confident, statuesque pose, soft natural breathing movement. No dialogue, no subtitles.",
        "Medium-full tracking pullback. Model walks slowly towards the camera, executes an effortless half-turn, and begins walking into the depth of the frame, showing back detailing. No dialogue, no subtitles."
      ]
    }
  ];

  /* Short labels for the dropdown: first clause of each template. */
  function shortLabel(text) {
    var cut = text.replace(/^(The |A |An )/, "").split(/[.,:]/)[0].trim();
    if (cut.length > 58) cut = cut.slice(0, 55).trim() + "\u2026";
    return cut;
  }

  window.AdsTemplates = {
    env: ENV_TEMPLATES,
    video: VIDEO_TEMPLATES,
    shortLabel: shortLabel,

    /* Build <optgroup> options for a <select>; returns option HTML. */
    optionsHtml: function (catalog) {
      var html = '<option value="">None</option>';
      (catalog || []).forEach(function (section) {
        html += '<optgroup label="' + section.group.replace(/&/g, "&") + '">';
        section.items.forEach(function (text, index) {
          var value = section.group + "::" + index;
          html += '<option value="' + value + '">' +
            window.AdsTemplates.shortLabel(text) + "</option>";
        });
        html += "</optgroup>";
      });
      return html;
    },

    /* Look up the full template text for a select value. */
    textFor: function (catalog, value) {
      if (!value) return "";
      var parts = String(value).split("::");
      var group = parts[0];
      var index = Number(parts[1]);
      for (var i = 0; i < (catalog || []).length; i += 1) {
        if (catalog[i].group === group) return catalog[i].items[index] || "";
      }
      return "";
    }
  };
})();
