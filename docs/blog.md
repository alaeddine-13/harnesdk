---
layout: default
title: Blog
permalink: /blog/
---

<div class="container">
  <div class="blog-header">
    <h1 class="section-title">Blog</h1>
    <p class="section-sub">Updates, tutorials, and deep dives from the HarneSDK team.</p>
  </div>

  <ul class="post-list">
    {% for post in site.posts %}
    <li class="post-item">
      <div class="post-meta">
        <span class="post-date">{{ post.date | date: "%B %d, %Y" }}</span>
        {% if post.tag %}<span class="post-tag">{{ post.tag }}</span>{% endif %}
      </div>
      <h2><a href="{{ post.url | prepend: site.baseurl }}">{{ post.title }}</a></h2>
      {% if post.excerpt %}
      <p class="post-excerpt">{{ post.excerpt | strip_html | truncatewords: 40 }}</p>
      {% endif %}
      <a href="{{ post.url | prepend: site.baseurl }}" class="read-more">Read more &rarr;</a>
    </li>
    {% endfor %}
  </ul>
</div>
