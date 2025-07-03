MySQL 쿼리

CREATE DATABASE IF NOT EXISTS final_join;
USE final_join;

CREATE TABLE IF NOT EXISTS users (
    id INT AUTO_INCREMENT PRIMARY KEY,
    last_name VARCHAR(50) NOT NULL,      -- 성
    first_name VARCHAR(50) NOT NULL,     -- 이름
    username VARCHAR(50) NOT NULL UNIQUE, -- Username (중복불가)
    email VARCHAR(100) NOT NULL UNIQUE,   -- Email address (중복불가)
    password VARCHAR(255) NOT NULL,       -- Password (해시 저장)
    notes TEXT                            -- Notes (메모, NULL 허용)
);