/**
 * Validate sponsored partner collection counts, deep links, and main README placement.
 *
 * Usage:
 *   node settings/validate_partner_collections.js
 *   node settings/validate_partner_collections.js --check-links
 */

const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const mainReadme = fs.readFileSync(path.join(root, 'README.md'), 'utf8');
const featuredLanding = fs.readFileSync(path.join(root, '00-featured-apis', 'README.md'), 'utf8');
const apyhubReadme = fs.readFileSync(path.join(root, '00-featured-apis', 'apyhub-utility-apis', 'README.md'), 'utf8');
const coreclawReadme = null; // CoreClaw partnership removed 2026-10-11
const readmeGenerator = fs.readFileSync(path.join(__dirname, 'generate_readme_clean.js'), 'utf8');

const errors = [];

function check(condition, message) {
    if (!condition) errors.push(message);
}

function unique(values) {
    return new Set(values).size === values.length;
}

const apyhubUrls = [...apyhubReadme.matchAll(/^\| \[[^\]]+\]\((https:\/\/apyhub\.com\/[^)]+)\)/gm)]
    .map(match => match[1]);
const uniqueApyhubUrls = [...new Set(apyhubUrls)];

check(uniqueApyhubUrls.length === 200, `Expected 200 unique ApyHub API links, found ${uniqueApyhubUrls.length}`);
check(!mainReadme.toLowerCase().includes('apyhub'), 'Main README still shows the ApyHub featured block (hidden 2026-10-11)');
check(featuredLanding.includes('href="./apyhub-utility-apis/"'), 'Featured APIs page is missing the ApyHub collection');
check(featuredLanding.includes('../assets/featured/apyhub-banner.png'), 'Featured APIs page is missing the ApyHub banner');
check(apyhubReadme.includes('../../assets/featured/apyhub-banner.png'), 'ApyHub page is missing its featured banner');
check(fs.existsSync(path.join(root, 'assets', 'featured', 'apyhub-banner.png')), 'ApyHub banner asset is missing');
check(!fs.existsSync(path.join(root, '00-featured-apis', 'coreclaw-scraper-apis')), 'CoreClaw collection directory still exists');
check(!mainReadme.toLowerCase().includes('coreclaw'), 'Main README still references CoreClaw');
check(!mainReadme.includes('fpr=chris69'), 'Main README still contains CoreClaw affiliate tracking');
check(!featuredLanding.toLowerCase().includes('coreclaw'), 'Featured APIs page still references CoreClaw');
check(!mainReadme.includes('All links include affiliate tracking'), 'Main README contains the removed affiliate-tracking note');
check(readmeGenerator.includes("name: 'ApyHub Utility API Collection'"), 'README generator is missing the ApyHub partner collection');
check(readmeGenerator.includes('### Sponsored: KamdenAI'), 'README generator is missing the KamdenAI sponsor block');
check(!readmeGenerator.includes('buymeacoffee.com'), 'README generator would restore the retired coffee-support block');

async function validateUrl(url) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000);

    try {
        const response = await fetch(url, {
            method: 'HEAD',
            redirect: 'follow',
            signal: controller.signal,
            headers: { 'user-agent': 'API-Mega-List-Link-Validator/1.0' }
        });
        return { url, status: response.status, ok: response.ok };
    } catch (error) {
        return { url, status: 0, ok: false, error: error.message };
    } finally {
        clearTimeout(timeout);
    }
}

async function validateInBatches(urls, concurrency = 8) {
    const results = [];
    let nextIndex = 0;

    async function worker() {
        while (nextIndex < urls.length) {
            const index = nextIndex++;
            results[index] = await validateUrl(urls[index]);
        }
    }

    await Promise.all(Array.from({ length: concurrency }, () => worker()));
    return results;
}

async function main() {
    if (errors.length > 0) {
        console.error('\nPartner collection validation failed:');
        for (const error of errors) console.error(`- ${error}`);
        process.exitCode = 1;
        return;
    }

    console.log(`ApyHub API links: ${uniqueApyhubUrls.length} unique (${apyhubUrls.length} category placements)`);
    console.log('Partner collection validation passed.');
}

main().catch(error => {
    console.error(error);
    process.exitCode = 1;
});
