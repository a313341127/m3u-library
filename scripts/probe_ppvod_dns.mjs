import dns from "node:dns";
const host = "ppvod01.6g9ba6.com";
try {
  const addrs = await dns.promises.lookup(host, { all: true });
  console.log(host + " DNS:", JSON.stringify(addrs));
  for (const a of addrs) {
    try {
      await new Promise((res, rej) => {
        const net = require("node:net");
        const s = net.connect(443, a.address, () => { s.end(); res(); });
        s.setTimeout(3000, () => { s.destroy(); rej(new Error("tcp timeout")); });
        s.on("error", rej);
      });
      console.log("  TCP 443 " + a.address + " OK");
    } catch (e) {
      console.log("  TCP 443 " + a.address + " FAIL " + e.message);
    }
  }
} catch (e) {
  console.log(host + " DNS resolve failed: " + e.message);
}
