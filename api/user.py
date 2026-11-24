"""用户端：商品浏览、购物车、下单、订单、评价、个人中心"""
from __future__ import annotations

import random
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from database import get_db
from models import CartItem, Order, OrderItem, Product, Review, Shop, UserProfile
from security import get_current_user, guard_page
from web import render

router = APIRouter(tags=["user"])


def _gen_order_no() -> str:
    return f"CS{datetime.now():%Y%m%d%H%M%S}{random.randint(100, 999)}"


# ===========================================================================
# 商品
# ===========================================================================
@router.get("/products")
def product_list(
    request: Request,
    q: str = "",
    category: str = "",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    stmt = (
        select(Product)
        .join(Shop, Product.shop_id == Shop.id)
        .where(Product.status == "on", Shop.status == "active")
    )
    if q.strip():
        stmt = stmt.where(Product.name.contains(q.strip()))
    if category.strip():
        stmt = stmt.where(Product.category == category.strip())

    products = db.scalars(stmt.order_by(Product.created_at.desc())).all()
    categories = db.scalars(select(Product.category).distinct()).all()
    return render(
        request,
        "user/products.html",
        user,
        products=products,
        categories=categories,
        q=q,
        category=category,
    )


@router.get("/products/{product_id}")
def product_detail(
    product_id: int,
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect
    product = db.get(Product, product_id)
    if product is None or product.status != "on":
        return RedirectResponse("/products?msg=商品已下架", status_code=303)
    return render(request, "user/product.html", user, product=product)


# ===========================================================================
# 购物车
# ===========================================================================
@router.get("/cart")
def cart_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    items = db.scalars(
        select(CartItem)
        .options(selectinload(CartItem.product))
        .where(CartItem.user_id == user.id)
        .order_by(CartItem.id.desc())
    ).all()
    total = sum(float(i.product.price) * i.quantity for i in items if i.product)
    return render(request, "user/cart.html", user, items=items, total=total)


@router.post("/cart/add")
def cart_add(
    product_id: int = Form(...),
    quantity: int = Form(1),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    product = db.get(Product, product_id)
    if product is None or product.status != "on":
        return RedirectResponse("/products?msg=商品已下架", status_code=303)

    quantity = max(1, min(quantity, product.stock or 1))
    item = db.scalar(
        select(CartItem).where(
            CartItem.user_id == user.id, CartItem.product_id == product_id
        )
    )
    if item is None:
        db.add(CartItem(user_id=user.id, product_id=product_id, quantity=quantity))
    else:
        item.quantity = min(item.quantity + quantity, product.stock)
    db.commit()
    return RedirectResponse("/cart", status_code=303)


@router.post("/cart/remove")
def cart_remove(
    item_id: int = Form(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    item = db.get(CartItem, item_id)
    if item is not None and item.user_id == user.id:
        db.delete(item)
        db.commit()
    return RedirectResponse("/cart", status_code=303)


# ===========================================================================
# 下单 / 订单
# ===========================================================================
@router.post("/checkout")
def checkout(
    receiver_name: str = Form(...),
    receiver_phone: str = Form(...),
    address: str = Form(...),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    items = db.scalars(
        select(CartItem)
        .options(selectinload(CartItem.product))
        .where(CartItem.user_id == user.id)
    ).all()
    if not items:
        return RedirectResponse("/cart?msg=购物车为空", status_code=303)

    for item in items:
        if item.product is None or item.product.status != "on":
            return RedirectResponse("/cart?msg=存在已下架商品，请先移除", status_code=303)
        if item.product.stock < item.quantity:
            return RedirectResponse(
                f"/cart?msg=「{item.product.name}」库存不足", status_code=303
            )

    # 按店铺拆单：一个店铺一张订单
    grouped: dict[int, list[CartItem]] = {}
    for item in items:
        grouped.setdefault(item.product.shop_id, []).append(item)

    for shop_id, shop_items in grouped.items():
        order = Order(
            order_no=_gen_order_no(),
            user_id=user.id,
            shop_id=shop_id,
            total_amount=sum(float(i.product.price) * i.quantity for i in shop_items),
            status="pending",
            receiver_name=receiver_name.strip(),
            receiver_phone=receiver_phone.strip(),
            address=address.strip(),
        )
        db.add(order)
        db.flush()
        for item in shop_items:
            db.add(
                OrderItem(
                    order_id=order.id,
                    product_id=item.product_id,
                    product_name=item.product.name,
                    price=item.product.price,
                    quantity=item.quantity,
                )
            )
            item.product.stock -= item.quantity
            db.delete(item)

    # 收货信息回填到用户档案，下次结算自动带出
    user.address = address.strip()
    user.phone = receiver_phone.strip()
    db.commit()
    return RedirectResponse("/orders?msg=下单成功，请完成支付", status_code=303)


@router.get("/orders")
def order_list(
    request: Request,
    tab: str = "all",
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    all_orders = db.scalars(
        select(Order)
        .options(selectinload(Order.items))
        .where(Order.user_id == user.id)
        .order_by(Order.created_at.desc())
    ).all()
    reviewed = set(
        db.scalars(select(Review.order_id).where(Review.user_id == user.id)).all()
    )

    # 订单分类：全部 / 待付款 / 待发货 / 待收货 / 待评价
    def match(order: Order, key: str) -> bool:
        if key == "pending":
            return order.status == "pending"
        if key == "ship":
            return order.status == "paid"
        if key == "receive":
            return order.status == "shipped"
        if key == "review":
            return order.status == "completed" and order.id not in reviewed
        return True

    counts = {
        key: sum(1 for o in all_orders if match(o, key))
        for key in ("all", "pending", "ship", "receive", "review")
    }
    orders = [o for o in all_orders if match(o, tab)]

    return render(
        request,
        "user/orders.html",
        user,
        orders=orders,
        reviewed=reviewed,
        tab=tab,
        counts=counts,
    )


def _own_order(db: Session, order_id: int, user_id: int) -> Order | None:
    order = db.get(Order, order_id)
    return order if order is not None and order.user_id == user_id else None


@router.post("/orders/{order_id}/pay")
def order_pay(
    order_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    order = _own_order(db, order_id, user.id)
    if order is not None and order.status == "pending":
        order.status = "paid"
        db.commit()
    return RedirectResponse("/orders?msg=支付成功（模拟支付）", status_code=303)


@router.post("/orders/{order_id}/cancel")
def order_cancel(
    order_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    order = _own_order(db, order_id, user.id)
    if order is not None and order.status in ("pending", "paid"):
        for item in order.items:
            product = db.get(Product, item.product_id)
            if product is not None:
                product.stock += item.quantity
        order.status = "cancelled"
        db.commit()
    return RedirectResponse("/orders?msg=订单已取消", status_code=303)


@router.post("/orders/{order_id}/confirm")
def order_confirm(
    order_id: int,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    order = _own_order(db, order_id, user.id)
    if order is not None and order.status == "shipped":
        order.status = "completed"
        order.delivered_at = datetime.now()
        db.commit()
    return RedirectResponse("/orders?msg=已确认收货", status_code=303)


@router.post("/orders/{order_id}/review")
def order_review(
    order_id: int,
    rating: int = Form(5),
    service_rating: int = Form(5),
    content: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    order = _own_order(db, order_id, user.id)
    if order is None or order.status != "completed":
        return RedirectResponse("/orders?msg=订单未完成，暂不能评价", status_code=303)

    exists = db.scalar(select(Review).where(Review.order_id == order.id))
    if exists is not None:
        return RedirectResponse("/orders?msg=该订单已评价", status_code=303)

    first_item = order.items[0] if order.items else None
    db.add(
        Review(
            order_id=order.id,
            product_id=first_item.product_id if first_item else 0,
            user_id=user.id,
            rating=max(1, min(5, rating)),
            service_rating=max(1, min(5, service_rating)),
            content=content.strip() or None,
        )
    )
    db.commit()
    return RedirectResponse("/orders?msg=评价成功，感谢你的反馈", status_code=303)


# ===========================================================================
# 个人中心
# ===========================================================================
@router.get("/profile")
def profile_page(
    request: Request,
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    order_count = len(db.scalars(select(Order.id).where(Order.user_id == user.id)).all())
    review_count = len(
        db.scalars(select(Review.id).where(Review.user_id == user.id)).all()
    )
    return render(
        request,
        "user/profile.html",
        user,
        profile=profile,
        order_count=order_count,
        review_count=review_count,
    )


@router.post("/profile")
def profile_update(
    nickname: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if redirect := guard_page(user, "customer"):
        return redirect

    user.phone = phone.strip() or None
    user.address = address.strip() or None

    profile = db.scalar(select(UserProfile).where(UserProfile.user_id == user.id))
    if profile is None:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
    profile.nickname = nickname.strip() or None
    db.commit()
    return RedirectResponse("/profile?msg=资料已保存", status_code=303)
