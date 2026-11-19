"""只沿已确认作品资料的可信引用取一小段公开采访；不做全库抓取或绕过访问限制。"""
from html.parser import HTMLParser
import json
import re
from urllib.parse import urlsplit
import httpx
from app import song_background as bg

MEDIA_HOSTS = {'bbc.com','bbc.co.uk','rollingstone.com','billboard.com','sonymusic.com','universalmusic.com'}


class Article(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title=[];self.paragraphs=[];self.current=[];self.in_title=False;self.in_p=False;self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style','nav','footer'):self.hidden+=1
        if tag=='title':self.in_title=True
        if tag=='p':self.in_p=True;self.current=[]
    def handle_endtag(self, tag):
        if tag in ('script','style','nav','footer'):self.hidden=max(0,self.hidden-1)
        if tag=='title':self.in_title=False
        if tag=='p' and self.in_p:
            self.paragraphs.append(''.join(self.current).strip());self.in_p=False
    def handle_data(self,data):
        if self.hidden:return
        if self.in_title:self.title.append(data)
        if self.in_p:self.current.append(data)


def _fetch(url):
    with httpx.Client(timeout=3,follow_redirects=False,headers={'User-Agent':'Personal-Radio/1.0 (source excerpt client)'}) as client:
        with client.stream('GET',url) as response:
            response.raise_for_status()
            if 'html' not in response.headers.get('content-type','').lower():return ''
            content=bytearray()
            for chunk in response.iter_bytes():
                content.extend(chunk)
                if len(content)>500_000:raise ValueError('采访页面过大')
            return content.decode('utf-8',errors='replace')


def get_interview_excerpt(song, facts, fetcher=None):
    """引用来自本曲已校验资料；标题还需同时匹配艺人和采访主题，避免只凭关键词串人。"""
    artist=str(song.get('artist') or '').strip()
    # 多人合作的身份不能靠随意拆分猜一位；先保守处理单一艺人的已链接采访。
    if not artist or re.search(r'[/、&]',artist):return []
    links=[f.get('url') for f in facts if isinstance(f,dict) and bg.safe_source_url(f.get('url'))
           and (urlsplit(f['url']).hostname or '').lower().removeprefix('www.') in MEDIA_HOSTS]
    if not links:return []
    url=links[0]  # 每批调用方也只尝试一首/一个已有引用，不遍历媒体网站。
    try:
        article=Article();article.feed((fetcher or _fetch)(url))
    except Exception:
        return []
    title=' '.join(article.title)
    if len(title)>140:return []
    if bg._norm(artist) not in bg._norm(title) or not re.search(r'采访|访谈|interview|in conversation',title,re.I):return []
    for paragraph in article.paragraphs:
        if bg._norm(artist) not in bg._norm(paragraph) or re.search(r'歌词|lyrics|https?://|订阅|subscribe',paragraph,re.I):continue
        for sentence in re.findall(r'[^。！？!?\r\n]+[。！？!?]|[^。！？!?\r\n]+?\.(?=\s|$)',paragraph):
            sentence=sentence.strip()
            if 12<=len(sentence)<=100 and len(sentence.split())<=25:
                return [{'id':'interview:'+song['id'], 'text':'公开采访文章《'+title+'》的原文短引：'+sentence,
                         'url':url,'entity_url':url,'source':'已有作品引用链接中的公开采访短引（媒体资料，非全篇考证）','kind':'interview_quote'}]
    return []
