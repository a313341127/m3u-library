const urls = [
  "https://ppvod01.6g9ba6.com/splitOut/20240707/292051/V2024070706045209508292051/index.m3u8",
  "https://ppvod01.6g9ba6.com/splitOut/20240721/408000/V2024072102490478742408000/index.m3u8",
];
for (const url of urls) {
  console.log("\n" + url.slice(0, 80) + "...");
  try {
    const r = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0", "Referer": "https://tv.libvio.cc/" },
    });
    const text = await r.text();
    console.log("  status=" + r.status + " len=" + text.length + " preview=" + text.slice(0, 120).replace(/\n/g, " "));
  } catch (e) {
    console.log("  ERROR " + e.message);
  }
}
