// Dynamic Menu JavaScript - МАКСИМАЛЬНО ПРОСТАЯ ВЕРСИЯ
document.addEventListener('DOMContentLoaded', function() {
    const dynamicMenuContainer = document.querySelector('.dynamic-menu-container');
    const dynamicMenuTrigger = document.querySelector('.dynamic-menu-trigger');
    const dynamicMenu = document.querySelector('.dynamic-menu');
    const dynamicMenuOverlay = document.querySelector('.dynamic-menu-overlay');
    const menuItems = document.querySelectorAll('.dynamic-menu-item');
    
    if (!dynamicMenuContainer || !dynamicMenuTrigger || !dynamicMenu) {
        console.error('Меню не найдено!');
        return;
    }
    
    let isMenuOpen = false;
    let clickBlocked = false; // Блокировка открытия после клика
    
    // Открыть меню
    function openMenu() {
        if (isMenuOpen || clickBlocked) return;
        isMenuOpen = true;
        dynamicMenuContainer.classList.add('menu-open');
        
        // Принудительно показываем меню через inline стили
        dynamicMenu.style.transform = 'translateX(0)';
        if (dynamicMenuOverlay) {
            dynamicMenuOverlay.style.opacity = '1';
            dynamicMenuOverlay.style.visibility = 'visible';
        }
        
        dynamicMenuTrigger.style.animation = 'none';
        menuItems.forEach((item, index) => {
            item.style.animationDelay = `${index * 0.1}s`;
            item.classList.add('animate-in');
        });
    }
    
    // Закрыть меню
    function closeMenu() {
        if (!isMenuOpen) return;
        isMenuOpen = false;
        dynamicMenuContainer.classList.remove('menu-open');
        
        // Принудительно скрываем меню через inline стили
        dynamicMenu.style.transform = 'translateX(-100%)';
        if (dynamicMenuOverlay) {
            dynamicMenuOverlay.style.opacity = '0';
            dynamicMenuOverlay.style.visibility = 'hidden';
        }
        
        dynamicMenuTrigger.style.animation = 'menuPulse 3s ease-in-out infinite';
        menuItems.forEach(item => {
            item.classList.remove('animate-in');
        });
    }
    
    // 1. Открытие при наведении на trigger
    dynamicMenuTrigger.addEventListener('mouseenter', function() {
        if (!clickBlocked) {
            openMenu();
        }
    });
    
    // 2. Закрытие при уходе с меню
    dynamicMenu.addEventListener('mouseleave', function(e) {
        if (!dynamicMenuTrigger.contains(e.relatedTarget)) {
            closeMenu();
        }
    });
    
    // 3. Закрытие при уходе с trigger
    dynamicMenuTrigger.addEventListener('mouseleave', function(e) {
        if (isMenuOpen && !dynamicMenu.contains(e.relatedTarget)) {
            closeMenu();
        }
    });
    
    // 4. ЗАКРЫТИЕ ПРИ КЛИКЕ ВНЕ МЕНЮ - САМОЕ ВАЖНОЕ
    document.addEventListener('click', function(e) {
        if (!isMenuOpen) return;
        
        // Если клик НЕ внутри контейнера меню - закрываем
        if (!dynamicMenuContainer.contains(e.target)) {
            clickBlocked = true; // Блокируем открытие
            closeMenu();
            
            // Разблокируем через 500мс
            setTimeout(() => {
                clickBlocked = false;
            }, 500);
        }
    }, true); // Capture phase - срабатывает первым
    
    // Эффекты для элементов меню
    menuItems.forEach(item => {
        item.addEventListener('click', function() {
            setTimeout(() => closeMenu(), 200);
        });
        
        item.addEventListener('mouseenter', function() {
            this.style.transform = 'translateX(10px) scale(1.02)';
        });
        
        item.addEventListener('mouseleave', function() {
            this.style.transform = 'translateX(0) scale(1)';
        });
    });
    
    // Клавиатура
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape' && isMenuOpen) {
            closeMenu();
        }
    });
});

// CSS анимации
const styleSheet = document.createElement('style');
styleSheet.textContent = `
    .dynamic-menu-container.menu-open .dynamic-menu {
        transform: translateX(0);
    }
    
    .dynamic-menu-container.menu-open .dynamic-menu-overlay {
        opacity: 1;
        visibility: visible;
    }
    
    .dynamic-menu-item.animate-in {
        animation: slideInLeft 0.3s ease forwards;
    }
    
    .dynamic-menu-trigger {
        transition: all 0.3s ease;
    }
    
    .dynamic-menu-item {
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
    }
`;
document.head.appendChild(styleSheet);
