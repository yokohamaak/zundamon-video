export default {
  async scheduled(event, env, ctx) {
    const res = await fetch(
      'https://api.github.com/repos/yokohamaak/news-digest-tts/actions/workflows/digest.yml/dispatches',
      {
        method: 'POST',
        headers: {
          Authorization: `token ${env.GITHUB_PAT}`,
          Accept: 'application/vnd.github.v3+json',
          'Content-Type': 'application/json',
          'User-Agent': 'news-digest-worker',
        },
        body: JSON.stringify({ ref: 'main' }),
      }
    );
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`GitHub API ${res.status}: ${text}`);
    }
  },
};
