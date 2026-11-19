// Static comment catalog for the simulated global chat.
//
// Organised by *intent slot* so the script generator can pull from a
// specific bucket (hype vs question vs emoji-only vs social-proof). The
// bucket structure is what gives the on-screen stream its natural shape:
// a burst of hype + emoji on every product change, with the occasional
// question / FOMO line breaking up the rhythm.
//
// Keeping this as plain data (no functions) is deliberate — the script
// generator is the only place we *compose* phrases. Behavioural rules
// (front-load density, group-excitement bursts) live in `CommentScript`.

export const CHAT_USERNAMES: readonly string[] = [
  "miarox",
  "thatslitsis",
  "drip_daddy",
  "noahsounds",
  "sock_lord_99",
  "abby.dee",
  "kevtok",
  "lulu_buys_things",
  "jordo.png",
  "fenwayfan",
  "mariposa.h",
  "luca_in_lima",
  "tessmarie",
  "yoyo_ohio",
  "rinarox",
  "graymatter",
  "sashasaystyle",
  "huxleyhouse",
  "joey.pdx",
  "kenzi.k",
  "amarafit",
  "petalpunk",
  "queenofdips",
  "owen.exe",
  "vibe_check_22",
  "bevvy.beans",
  "noor.in.nyc",
  "rae.collects",
  "tinytomato",
  "milobass",
];

/** Short hype lines — front-loaded on product change. */
export const HYPE_LINES: readonly string[] = [
  "ok this is sick",
  "OMG I need this",
  "okay this is iconic",
  "this is everything",
  "wait this is so good",
  "OBSESSED",
  "ITS GIVING",
  "okay you sold me",
  "this is unreal",
  "running to buy",
  "wow wow wow",
  "the colorway tho",
  "im literally buying this",
  "this lives in my brain rent free",
  "she went off",
  "this slaps",
  "stop it i love this",
  "no bc this is fire",
];

/** Questions a real shopper would ask mid-pitch. */
export const QUESTION_LINES: readonly string[] = [
  "does it run small?",
  "is this still in stock?",
  "what sizes left?",
  "comes in other colors?",
  "shipping how long?",
  "is this washable?",
  "any code today?",
  "is there a bundle?",
  "warranty on this?",
  "fits a size 9?",
  "any reviews?",
  "is it heavy?",
];

/** Social-proof / FOMO lines — the lines that make new arrivals feel
 *  the room is moving. */
export const SOCIAL_PROOF_LINES: readonly string[] = [
  "you guys are buying these so fast",
  "i bought one last drop",
  "third one im snagging",
  "already in my cart",
  "my friend has this and loves it",
  "tip: get two",
  "last drop sold out fast fr",
  "gifting one to my sister",
  "treat yourself",
  "this paid for itself for me",
];

/** Emoji-only / reaction lines. */
export const REACTION_LINES: readonly string[] = [
  "🔥🔥🔥",
  "😍😍",
  "👏👏👏",
  "🛒🛒",
  "💯",
  "❤️❤️",
  "🤩",
  "👀👀",
  "💸💸",
  "ahhhh 😭",
  "🥹",
  "🫶🫶",
];

/** Group-excitement burst seeds — when the script picks a burst window,
 *  it draws three consecutive lines from the same seed family so the
 *  feed reads as the room reacting *together* to the same moment. */
export const BURST_FAMILIES: readonly (readonly string[])[] = [
  ["FIRE 🔥", "this is fire 🔥", "🔥🔥🔥"],
  ["i NEED this", "i need this i need this", "need 😩"],
  ["okay take my money", "shut up and take my money", "💸💸💸"],
  ["how is this real", "this cant be real", "wait is this real"],
  ["BUY BUY BUY", "buying now", "in my cart already 🛒"],
  ["the color 😍", "that color tho", "color is unreal"],
];

/** Highlight phrases — fragments the renderer will paint in brand-accent
 *  orange when present in a message. Order matters: longer fragments
 *  first so we don't partial-match a substring. */
export const HIGHLIGHT_FRAGMENTS: readonly string[] = [
  "BUY BUY BUY",
  "FIRE 🔥",
  "OBSESSED",
  "ITS GIVING",
  "🔥🔥🔥",
  "OMG",
];
