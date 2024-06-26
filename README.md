``` mermaid
graph TD
    A[Client] -->|1. Запрос на регистрацию/авторизацию| B[FastAPI сервер]
    B -->|2. Запрос к БД через SQLAlchemy| C[PostgreSQL]
    C -->|3. Ответ от БД| B
    B -->|4. JWT токен| A
    
    A -->|5. Запрос на загрузку файла| B
    B -->|6. Проверка токена и прав| C
    B -->|7. Запрос к Redis на получение кэша| E[Redis]
    E -->|8. Ответ от Redis| B
    
    B -->|9. Проверка типа файла| B
    B -->|10. Сохранение файла на сервере| B
    B -->|11. Запись информации о файле в БД| C
    
    B -->|12. Запуск асинхронной задачи по обработке файла| D[Фоновая задача]
    D -->|13. Чтение файла| D
    D -->|14. Парсинг файла с использованием pyparsing| D
    D -->|15. Модификация кода| D
    D -->|16. Сохранение изменений| D
    D -->|17. Обновление статуса файла в БД| C
    
    A -->|18. Запрос на статус файла| B
    B -->|19. Получение статуса из БД| C
    C -->|20. Ответ со статусом файла| B
    B -->|21. Ответ клиенту с текущим статусом файла| A
    
    A -->|22. Запрос на скачивание файла| B
    B -->|23. Проверка токена и прав| C
    B -->|24. Отправка файла клиенту| A
```
``` mermaid
graph TD
    A[Client] -->|1. GET запрос| B[FastAPI сервер]
    B -->|2. Генерация HTML| B
    B -->|3. Поиск данных в Redis| E[Redis]
    E -->|4. Данные найдены?| B
    E -->|5. Данные не найдены| C[PostgreSQL]
    C -->|6. Запрос данных| C
    C -->|7. Ответ с данными| B
    B -->|8. Кэширование данных| E
    E -->|9. Кэширование завершено| B
    B -->|10. Генерация HTML с данными| B
    B -->|11. Ответ с HTML| A
```
