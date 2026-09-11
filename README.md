# arxiv GraphRAG

arxiv 논문들 중 대 분류를 computer science로 한정 지은 후 아래 소분류들의 논문들을 정리

기본 저자(autor), 논문(title, abstract), 주제(categories)를
(:Autor)-[:WRITED_BY]->(:Paper)
(:Paper)-[:IN_CATEGORY]->(:Category)
(:Paper)-[:DATE_BY]->(:date)
으로 연관짓고

시간이 남을시 기간을 늘려 데이터를 더 모으거나 